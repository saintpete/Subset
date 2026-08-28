"""OBS control over obs-websocket 5.x.

Builds (idempotently) a "Subset" scene: the capture card's video, its
audio set to monitor-only (so the TV hears the show), and Netflix-style
captions — white bold text with a drop shadow, no background band, each
line center-aligned. FreeType can't center multi-line text, so every
caption line is its own text source anchored to the canvas centerline;
set_text() distributes wrapped lines across them, bottom-up.
"""

import asyncio
import contextlib

import simpleobsws

SCENE = "Subset"
VIDEO_INPUT = "Subset Video"
AUDIO_INPUT = "Subset Audio"
TEXT_PREFIX = "Subset Caption Line"

# Inputs from earlier scene layouts, removed on sight.
_LEGACY_INPUTS = ("Subset Captions", "Subset Caption Backdrop")

CANVAS_W, CANVAS_H = 1920, 1080
_BOTTOM_MARGIN = 80  # gap under the last caption line, Netflix-ish

# OBS stores colors as 0xAABBGGRR ints.
_TEXT_COLOR = 0xFFFFFFFF


class ObsCaptioner:
    def __init__(self, url: str, password: str | None) -> None:
        self._url = url
        self._password = password or None
        self._ensure_args: tuple | None = None
        self._lines: list[str] = []
        self._ws = simpleobsws.WebSocketClient(url=url, password=self._password)

    async def reconnect_and_repair(self) -> None:
        """After an OBS restart: fresh socket, then re-assert the scene."""
        with contextlib.suppress(Exception):
            await self._ws.disconnect()
        self._ws = simpleobsws.WebSocketClient(url=self._url, password=self._password)
        await self._ws.connect()
        await self._ws.wait_until_identified()
        if self._ensure_args is not None:
            await self.ensure_scene(*self._ensure_args)

    async def connect(self, attempts: int = 5) -> None:
        last: Exception | None = None
        for _ in range(attempts):
            try:
                await self._ws.connect()
                await self._ws.wait_until_identified()
                return
            except Exception as exc:
                last = exc
                await asyncio.sleep(2)
        raise RuntimeError(
            f"Could not reach OBS WebSocket ({last}). Is OBS running with "
            "Tools → WebSocket Server Settings enabled, and OBS_WS_PASSWORD set in .env?"
        )

    async def close(self) -> None:
        await self._ws.disconnect()

    async def call(self, request: str, data: dict | None = None) -> dict:
        resp = await self._ws.call(simpleobsws.Request(request, data or {}))
        if not resp.ok():
            raise RuntimeError(f"OBS request {request} failed: {resp.requestStatus}")
        return resp.responseData or {}

    async def set_text(self, text: str) -> None:
        """Distribute wrapped lines across the per-line sources, bottom-up."""
        lines = [line for line in text.split("\n") if line] if text else []
        lines = lines[-len(self._lines) :]
        padded = [""] * (len(self._lines) - len(lines)) + lines
        for name, line in zip(self._lines, padded):
            await self.call(
                "SetInputSettings",
                {"inputName": name, "inputSettings": {"text": line}, "overlay": True},
            )

    # -- scene construction -------------------------------------------------

    async def ensure_scene(
        self,
        video_substr: str,
        audio_substr: str,
        font_size: int,
        max_lines: int,
        monitor_audio: bool = True,
    ) -> None:
        self._ensure_args = (video_substr, audio_substr, font_size, max_lines, monitor_audio)
        kinds = (await self.call("GetInputKindList"))["inputKinds"]
        await self.call(
            "SetVideoSettings",
            {
                "baseWidth": CANVAS_W,
                "baseHeight": CANVAS_H,
                "outputWidth": CANVAS_W,
                "outputHeight": CANVAS_H,
                "fpsNumerator": 60,
                "fpsDenominator": 1,
            },
        )

        scenes = {s["sceneName"] for s in (await self.call("GetSceneList"))["scenes"]}
        if SCENE not in scenes:
            await self.call("CreateScene", {"sceneName": SCENE})

        inputs = {i["inputName"] for i in (await self.call("GetInputList"))["inputs"]}

        video_kind = self._pick_kind(kinds, ["av_capture_input_v2", "av_capture_input"])
        if VIDEO_INPUT not in inputs:
            await self.call(
                "CreateInput",
                {
                    "sceneName": SCENE,
                    "inputName": VIDEO_INPUT,
                    "inputKind": video_kind,
                    "inputSettings": {"buffering": False},
                },
            )
            prop, value, label = await self._pick_device(
                VIDEO_INPUT, ["device", "device_id"], video_substr
            )
            await self.call(
                "SetInputSettings",
                {
                    "inputName": VIDEO_INPUT,
                    "inputSettings": {prop: value, "device_name": label},
                    "overlay": True,
                },
            )

        audio_kind = self._pick_kind(kinds, ["coreaudio_input_capture"])
        if AUDIO_INPUT not in inputs:
            await self.call(
                "CreateInput",
                {"sceneName": SCENE, "inputName": AUDIO_INPUT, "inputKind": audio_kind},
            )
            prop, value, _ = await self._pick_device(
                AUDIO_INPUT, ["device_id", "device"], audio_substr
            )
            await self.call(
                "SetInputSettings",
                {"inputName": AUDIO_INPUT, "inputSettings": {prop: value}, "overlay": True},
            )
        monitor_type = (
            "OBS_MONITORING_TYPE_MONITOR_ONLY"
            if monitor_audio
            else "OBS_MONITORING_TYPE_NONE"  # app-level passthrough owns audio
        )
        await self.call(
            "SetInputAudioMonitorType",
            {"inputName": AUDIO_INPUT, "monitorType": monitor_type},
        )
        # A stray click on the mixer's speaker icon silently kills TV audio;
        # assert the working state on every startup.
        await self.call(
            "SetInputMute", {"inputName": AUDIO_INPUT, "inputMuted": False}
        )

        for legacy in _LEGACY_INPUTS:
            with contextlib.suppress(RuntimeError):
                await self.call("RemoveInput", {"inputName": legacy})

        text_kind = self._pick_kind(kinds, ["text_ft2_source_v2", "text_ft2_source"])
        self._lines = [f"{TEXT_PREFIX} {i + 1}" for i in range(max_lines)]
        for name in self._lines:
            if name in inputs:
                continue
            await self.call(
                "CreateInput",
                {
                    "sceneName": SCENE,
                    "inputName": name,
                    "inputKind": text_kind,
                    "inputSettings": {
                        "font": {
                            "face": "Helvetica Neue",
                            "style": "Bold",
                            "size": font_size,
                        },
                        "color1": _TEXT_COLOR,
                        "color2": _TEXT_COLOR,
                        "outline": False,
                        "drop_shadow": True,
                        "text": "",
                    },
                },
            )

        await self._layout(font_size, max_lines)
        await self.call("SetCurrentProgramScene", {"sceneName": SCENE})

    async def _layout(self, font_size: int, max_lines: int) -> None:
        async def place(source: str, transform: dict, index: int) -> None:
            item = await self.call(
                "GetSceneItemId", {"sceneName": SCENE, "sourceName": source}
            )
            item_id = item["sceneItemId"]
            await self.call(
                "SetSceneItemTransform",
                {"sceneName": SCENE, "sceneItemId": item_id, "sceneItemTransform": transform},
            )
            await self.call(
                "SetSceneItemIndex",
                {"sceneName": SCENE, "sceneItemId": item_id, "sceneItemIndex": index},
            )

        await place(
            VIDEO_INPUT,
            {
                "positionX": 0,
                "positionY": 0,
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "boundsAlignment": 0,
                "boundsWidth": CANVAS_W,
                "boundsHeight": CANVAS_H,
            },
            0,
        )
        line_height = round(font_size * 1.3)
        base_y = CANVAS_H - _BOTTOM_MARGIN - line_height * max_lines
        for i, name in enumerate(self._lines):
            await place(
                name,
                {
                    "positionX": CANVAS_W / 2,
                    "positionY": base_y + i * line_height,
                    "alignment": 4,  # anchor: top edge, horizontally centered
                },
                i + 1,
            )

    @staticmethod
    def _pick_kind(kinds: list[str], candidates: list[str]) -> str:
        for kind in candidates:
            if kind in kinds:
                return kind
        raise RuntimeError(f"None of {candidates} available in OBS (have: {kinds})")

    async def _pick_device(
        self, input_name: str, prop_names: list[str], substr: str
    ) -> tuple[str, str, str]:
        for prop in prop_names:
            try:
                items = (
                    await self.call(
                        "GetInputPropertiesListPropertyItems",
                        {"inputName": input_name, "propertyName": prop},
                    )
                )["propertyItems"]
            except RuntimeError:
                continue
            if not items:
                continue
            for item in items:
                if substr.lower() in str(item.get("itemName", "")).lower():
                    return prop, item["itemValue"], str(item["itemName"])
            available = [item.get("itemName") for item in items]
            raise RuntimeError(
                f"No device matching {substr!r} for {input_name}; OBS sees: {available}"
            )
        raise RuntimeError(f"Could not enumerate devices for {input_name}")
