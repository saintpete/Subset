"""OBS control over obs-websocket 5.x.

Builds (idempotently) a "Subset" scene containing the capture card's
video, its audio set to monitor-only (so the TV hears the show), a
caption backdrop band, and the caption text source this app updates.
"""

import asyncio

import simpleobsws

SCENE = "Subset"
VIDEO_INPUT = "Subset Video"
AUDIO_INPUT = "Subset Audio"
BACKDROP_INPUT = "Subset Caption Backdrop"
TEXT_INPUT = "Subset Captions"

CANVAS_W, CANVAS_H = 1920, 1080
CAPTION_W = 1760
BACKDROP_H = 170

# OBS stores colors as 0xAABBGGRR ints.
_BACKDROP_COLOR = 0x99000000  # black, ~60% opaque
_TEXT_COLOR = 0xFFFFFFFF


class ObsCaptioner:
    def __init__(self, url: str, password: str | None) -> None:
        self._ws = simpleobsws.WebSocketClient(url=url, password=password or None)

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
        await self.call(
            "SetInputSettings",
            {"inputName": TEXT_INPUT, "inputSettings": {"text": text}, "overlay": True},
        )

    # -- scene construction -------------------------------------------------

    async def ensure_scene(self, video_substr: str, audio_substr: str, font_size: int) -> None:
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
        await self.call(
            "SetInputAudioMonitorType",
            {"inputName": AUDIO_INPUT, "monitorType": "OBS_MONITORING_TYPE_MONITOR_ONLY"},
        )

        backdrop_kind = self._pick_kind(kinds, ["color_source_v3", "color_source"])
        if BACKDROP_INPUT not in inputs:
            await self.call(
                "CreateInput",
                {
                    "sceneName": SCENE,
                    "inputName": BACKDROP_INPUT,
                    "inputKind": backdrop_kind,
                    "inputSettings": {
                        "color": _BACKDROP_COLOR,
                        "width": CANVAS_W,
                        "height": BACKDROP_H,
                    },
                },
            )

        text_kind = self._pick_kind(kinds, ["text_ft2_source_v2", "text_ft2_source"])
        if TEXT_INPUT not in inputs:
            await self.call(
                "CreateInput",
                {
                    "sceneName": SCENE,
                    "inputName": TEXT_INPUT,
                    "inputKind": text_kind,
                    "inputSettings": {
                        "font": {"face": "Helvetica", "style": "Bold", "size": font_size},
                        "color1": _TEXT_COLOR,
                        "color2": _TEXT_COLOR,
                        "outline": True,
                        "word_wrap": True,
                        "custom_width": CAPTION_W,
                        "text": "",
                    },
                },
            )

        await self._layout()
        await self.call("SetCurrentProgramScene", {"sceneName": SCENE})

    async def _layout(self) -> None:
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
        await place(
            BACKDROP_INPUT,
            {"positionX": 0, "positionY": CANVAS_H - BACKDROP_H - 20, "alignment": 5},
            1,
        )
        await place(
            TEXT_INPUT,
            {
                "positionX": (CANVAS_W - CAPTION_W) / 2,
                "positionY": CANVAS_H - BACKDROP_H - 5,
                "alignment": 5,  # top-left anchor
            },
            2,
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
