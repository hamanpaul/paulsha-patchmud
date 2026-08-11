"""agy CLI headless adapter（Google Gemini，OAuth 登入態；issue #14）。

以 ``agy --print`` 非互動模式執行，認證取自 ``~/.antigravitycli``——不需要
任何 API key 環境變數。

純補全模式的硬性旗標（見 :mod:`patchmud.adapters.cli_base` 的理由）：

``--sandbox``
    終端限制模式；candidate code 的執行仍只走 ``IsolationRunner``。
``--disable-slash-commands``
    關閉 slash command 與 skill 展開，避免關卡文字被當成指令解讀。
``--effort``
    固定 ``high``。agy 的 effort 也可以烘在 model id 後綴
    （``gemini-3.6-flash-high``），這裡改用 base id + 顯式旗標，與
    :class:`~patchmud.adapters.codex_cli.CodexCliAdapter` 對稱，別名表也
    不必為每個 effort 檔位各列一條。

輸出解析（``--output-format json`` 的單一 JSON object）::

    {"status":"SUCCESS","response":"…","usage":{"input_tokens":…,
     "output_tokens":…,"thinking_tokens":…,"cache_read_tokens":…,
     "total_tokens":…}}

``status`` 非 ``SUCCESS`` 一律 fail-closed——CLI 以 exit 0 回報的失敗不得被
當成模型的合法回覆。``usage`` 原樣透傳給 ledger 的 ``agy`` mapper。
"""

from __future__ import annotations

from patchmud.adapters.base import AdapterError
from patchmud.adapters.cli_base import CliModelAdapter, iter_json_objects

__all__ = ["AgyCliAdapter"]

SUCCESS_STATUS = "SUCCESS"


class AgyCliAdapter(CliModelAdapter):
    """``agy --print`` 純補全 adapter。"""

    usage_provider = "agy"
    binary_name = "agy"

    def __init__(
        self,
        model: str = "gemini-3.6-flash",
        *,
        agy_binary: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(model, binary=agy_binary, **kwargs)

    def _build_argv(self, prompt: str) -> list[str]:
        return [
            self._binary,
            "--print",
            prompt,
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--output-format",
            "json",
            "--disable-slash-commands",
            "--sandbox",
        ]

    def _parse(self, stdout: str) -> tuple[str, dict]:
        payload: dict | None = None
        for candidate in iter_json_objects(stdout):
            if "response" in candidate or "status" in candidate:
                payload = candidate
        if payload is None:
            raise AdapterError(
                f"agy 輸出不是合法的 JSON 結果（fail-closed）：{stdout.strip()[:300]}"
            )

        status = payload.get("status")
        if status != SUCCESS_STATUS:
            raise AdapterError(f"agy 回報非成功狀態：status={status!r}")

        response = payload.get("response")
        if not isinstance(response, str):
            raise AdapterError(f"agy 回應缺 response 字串：{response!r}")

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise AdapterError("agy 回應缺 usage metadata（ledger 無從計費）")

        return response.strip(), usage
