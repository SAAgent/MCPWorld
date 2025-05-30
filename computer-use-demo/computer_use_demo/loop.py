"""
Agentic sampling loop that calls the Anthropic API and local implementation of anthropic-defined computer use tools.
"""

import platform
import os
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, cast, Optional, List, Dict
import time

import httpx
from anthropic import (
    Anthropic,
    AnthropicBedrock,
    AnthropicVertex,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    DefaultHttpxClient
)
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaContentBlockParam,
    BetaImageBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
    BetaToolUseBlockParam,
)

from .tools import (
    TOOL_GROUPS_BY_VERSION,
    ToolCollection,
    ToolResult,
    ToolVersion,
)

from .mcpclient import MCPClient

PROMPT_CACHING_BETA_FLAG = "prompt-caching-2024-07-31"

try:
    from evaluator.core.base_evaluator import BaseEvaluator
    from evaluator.core.events import AgentEvent
except ImportError:
    BaseEvaluator = None
    AgentEvent = None


class APIProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"
    VERTEX = "vertex"


# This system prompt is optimized for the Docker environment in this repository and
# specific tool combinations enabled.
# We encourage modifying this system prompt to ensure the model has context for the
# environment it is running in, and to provide any additional information that may be
# helpful for the task at hand.
SYSTEM_PROMPT = f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using {platform.machine()} architecture with internet access.
* You can feel free to install Ubuntu applications with your bash tool. Use curl instead of wget.
* To open firefox, please just click on the firefox icon.  Note, firefox-esr is what is installed on your system.
* Using bash tool you can start GUI applications, but you need to set export DISPLAY={os.getenv("DISPLAY")} and use a subshell. For example "(DISPLAY={os.getenv("DISPLAY")} xterm &)". GUI apps run with bash tool will appear within your desktop environment, but they may take some time to appear. Take a screenshot to confirm it did.
* When using your bash tool with commands that are expected to output very large quantities of text, redirect into a tmp file and use str_replace_editor or `grep -n -B <lines before> -A <lines after> <query> <filename>` to confirm output.
* When viewing a page it can be helpful to zoom out so that you can see everything on the page.  Either that, or make sure you scroll down to see everything before deciding something isn't available.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>

<IMPORTANT>
* When using Firefox, if a startup wizard appears, IGNORE IT.  Do not even click "skip this step".  Instead, click on the address bar where it says "Search or enter address", and enter the appropriate search term or URL there.
* If the item you are looking at is a pdf, if after taking a single screenshot of the pdf it seems that you want to read the entire document instead of trying to continue to read the pdf from your screenshots + navigation, determine the URL, use curl to download the pdf, install and use pdftotext to convert it to a text file, and then read that text file directly with your StrReplaceEditTool.
</IMPORTANT>"""

SYSTEM_PROMPT_API_ONLY = f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using {platform.machine()} architecture with internet access.
* You can feel free to install Ubuntu applications with your bash tool. Use curl instead of wget.
* When using your bash tool with commands that are expected to output very large quantities of text, redirect into a tmp file and use str_replace_editor or `grep -n -B <lines before> -A <lines after> <query> <filename>` to confirm output.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>

<IMPORTANT>
* If the item you are looking at is a pdf, if after taking a single screenshot of the pdf it seems that you want to read the entire document instead of trying to continue to read the pdf from your screenshots + navigation, determine the URL, use curl to download the pdf, install and use pdftotext to convert it to a text file, and then read that text file directly with your StrReplaceEditTool.
</IMPORTANT>"""

SYSTEM_PROMPT_NO_BASH = f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using {platform.machine()} architecture with internet access.
* To open firefox, please just click on the firefox icon.  Note, firefox-esr is what is installed on your system.
* When viewing a page it can be helpful to zoom out so that you can see everything on the page.  Either that, or make sure you scroll down to see everything before deciding something isn't available.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>

<IMPORTANT>
* When using Firefox, if a startup wizard appears, IGNORE IT.  Do not even click "skip this step".  Instead, click on the address bar where it says "Search or enter address", and enter the appropriate search term or URL there.
</IMPORTANT>"""

SYSTEM_PROMPT_NO_BASH_API_ONLY = f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using {platform.machine()} architecture with internet access.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>
"""


# --- Evaluator Helper Functions ---
def _record_tool_call_start(
    evaluator: Optional[BaseEvaluator],
    task_id: Optional[str],
    tool_name: str,
    tool_input: Dict[str, Any]
):
    start_time = time.time()
    """Records the TOOL_CALL_START event if evaluator is enabled."""
    if evaluator and task_id and AgentEvent:
        try:
            evaluator.record_event(
                AgentEvent.TOOL_CALL_START,
                {
                    "timestamp": start_time,
                    "tool_name": tool_name,
                    "args": tool_input,
                }
            )
        except Exception as rec_e:
            print(f"[Evaluator Error] Failed to record TOOL_CALL_START: {rec_e}")

def _record_tool_call_end(
    evaluator: Optional[BaseEvaluator],
    task_id: Optional[str],
    tool_name: str,
    tool_result: ToolResult,
):
    end_time = time.time()
    """Records the TOOL_CALL_END event if evaluator is enabled."""
    if evaluator and task_id and AgentEvent:
        try:
            tool_success = not tool_result.error
            tool_error = tool_result.error

            event_data = {
                "timestamp": end_time,
                "tool_name": tool_name,
                "success": tool_success,
                "error": tool_error,
                "result": None,
            }

            if tool_success:
                if tool_result.output:
                    output_str = str(tool_result.output)
                    if len(output_str) > 1000:
                        event_data["result"] = output_str[:500] + "... (truncated)"
                    else:
                        event_data["result"] = output_str
                elif tool_result.base64_image:
                    event_data["result"] = "[Screenshot Taken]"
            else:
                event_data["result"] = tool_result.error

            evaluator.record_event(
                AgentEvent.TOOL_CALL_END,
                event_data
            )
        except Exception as rec_e:
            print(f"[Evaluator Error] Failed to record TOOL_CALL_END: {rec_e}")
# --- End Evaluator Helper Functions ---


async def sampling_loop(
    *,
    model: str,
    provider: APIProvider,
    system_prompt_suffix: str,
    messages: list[BetaMessageParam],
    output_callback: Callable[[BetaContentBlockParam], None],
    tool_output_callback: Callable[[ToolResult, str], None],
    api_response_callback: Callable[
        [httpx.Request, httpx.Response | object | None, Exception | None], None
    ],
    api_key: str,
    evaluator: Optional[BaseEvaluator] = None,
    evaluator_task_id: Optional[str] = None,
    is_timeout: Callable[[], bool],
    only_n_most_recent_images: int | None = None,
    max_tokens: int = 4096,
    tool_version: ToolVersion,
    thinking_budget: int | None = None,
    token_efficient_tools_beta: bool = False,
):
    """
    Agentic sampling loop for the assistant/tool interaction of computer use.
    """
    mcp_servers = evaluator.config.get("mcp_servers", [])
    mcp_client = MCPClient()
    try:
        tool_group = TOOL_GROUPS_BY_VERSION[tool_version]
        exec_mode = evaluator.config.get("exec_mode", "mixed")
        if exec_mode == "api":
            for tool in tool_group.tools:
                if "computer" in tool.name:
                    tool_group.tools.remove(tool)
        tool_collection = ToolCollection(*(ToolCls() for ToolCls in tool_group.tools))
        all_tool_list = tool_collection.to_params()
        if exec_mode in ["mixed", "api"]:
            for server in mcp_servers:
                await mcp_client.connect_to_server(server)
            mcp_tools = await mcp_client.list_tools()
            all_tool_list.extend(mcp_tools)

        if tool_version == "computer_only":
            system = BetaTextBlockParam(
                type="text",
                text=f"{SYSTEM_PROMPT_NO_BASH_API_ONLY if exec_mode == 'api' else SYSTEM_PROMPT_NO_BASH}{' ' + system_prompt_suffix if system_prompt_suffix else ''}",
            )
        else:
            system = BetaTextBlockParam(
                type="text",
                text=f"{SYSTEM_PROMPT_API_ONLY if exec_mode == 'api' else SYSTEM_PROMPT}{' ' + system_prompt_suffix if system_prompt_suffix else ''}",
            )        

        while not is_timeout():
            enable_prompt_caching = False
            betas = [tool_group.beta_flag] if tool_group.beta_flag else []
            if token_efficient_tools_beta:
                betas.append("token-efficient-tools-2025-02-19")
            image_truncation_threshold = only_n_most_recent_images or 0
            if provider == APIProvider.ANTHROPIC:
                client = Anthropic(api_key=api_key, max_retries=4, http_client=httpx.Client(proxy="http://10.161.28.28:10809"))
                enable_prompt_caching = True
            elif provider == APIProvider.VERTEX:
                client = AnthropicVertex()
            elif provider == APIProvider.BEDROCK:
                client = AnthropicBedrock()

            if enable_prompt_caching:
                betas.append(PROMPT_CACHING_BETA_FLAG)
                _inject_prompt_caching(messages)
                # Because cached reads are 10% of the price, we don't think it's
                # ever sensible to break the cache by truncating images
                only_n_most_recent_images = 0
                # Use type ignore to bypass TypedDict check until SDK types are updated
                system["cache_control"] = {"type": "ephemeral"}  # type: ignore

            if only_n_most_recent_images:
                _maybe_filter_to_n_most_recent_images(
                    messages,
                    only_n_most_recent_images,
                    min_removal_threshold=image_truncation_threshold,
                )
            extra_body = {}
            if thinking_budget:
                # Ensure we only send the required fields for thinking
                extra_body = {
                    "thinking": {"type": "enabled", "budget_tokens": thinking_budget}
                }

            # Call the API
            # we use raw_response to provide debug information to streamlit. Your
            # implementation may be able call the SDK directly with:
            # `response = client.messages.create(...)` instead.
            try:
                raw_response = client.beta.messages.with_raw_response.create(
                    max_tokens=max_tokens,
                    messages=messages,
                    model=model,
                    system=[system],
                    tools=all_tool_list,
                    betas=betas,
                    extra_body=extra_body,
                    temperature=0,
                )
            except (APIStatusError, APIResponseValidationError) as e:
                api_response_callback(e.request, e.response, e)
                return messages
            except APIError as e:
                api_response_callback(e.request, e.body, e)
                return messages

            api_response_callback(
                raw_response.http_response.request, raw_response.http_response, None
            )

            response = raw_response.parse()

            response_params = _response_to_params(response)
            messages.append(
                {
                    "role": "assistant",
                    "content": response_params,
                }
            )

            tool_result_content: list[BetaToolResultBlockParam] = []
            for content_block in response_params:
                output_callback(content_block)
                if content_block["type"] == "tool_use":
                    tool_name = content_block["name"]
                    tool_input = cast(dict[str, Any], content_block["input"])
                    result: Optional[ToolResult] = None

                    # --- Record Tool Start ---
                    _record_tool_call_start(
                        evaluator, evaluator_task_id, tool_name, tool_input
                    )
                    # --- End Record Tool Start ---
                    if content_block["name"] in tool_collection.tool_map.keys():
                        result = await tool_collection.run(
                            name=content_block["name"],
                            tool_input=cast(dict[str, Any], content_block["input"]),
                        )
                    else:
                        result = await mcp_client.call_tool(
                            name=content_block["name"],
                            tool_input=cast(dict[str, Any], content_block["input"]),
                        )
                    # --- End Record Tool Start ---
                    _record_tool_call_end(
                        evaluator, evaluator_task_id, tool_name, result
                    )
                    # --- End Record Tool End ---

                    tool_result_content.append(
                        _make_api_tool_result(result, content_block["id"])
                    )
                    tool_output_callback(result, content_block["id"])

            if not tool_result_content:
                return messages

            messages.append({"content": tool_result_content, "role": "user"})
    finally:
        await mcp_client.cleanup()


def _maybe_filter_to_n_most_recent_images(
    messages: list[BetaMessageParam],
    images_to_keep: int,
    min_removal_threshold: int,
):
    """
    With the assumption that images are screenshots that are of diminishing value as
    the conversation progresses, remove all but the final `images_to_keep` tool_result
    images in place, with a chunk of min_removal_threshold to reduce the amount we
    break the implicit prompt cache.
    """
    if images_to_keep is None:
        return messages

    tool_result_blocks = cast(
        list[BetaToolResultBlockParam],
        [
            item
            for message in messages
            for item in (
                message["content"] if isinstance(message["content"], list) else []
            )
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ],
    )

    total_images = sum(
        1
        for tool_result in tool_result_blocks
        for content in tool_result.get("content", [])
        if isinstance(content, dict) and content.get("type") == "image"
    )

    images_to_remove = total_images - images_to_keep
    # for better cache behavior, we want to remove in chunks
    images_to_remove -= images_to_remove % min_removal_threshold

    for tool_result in tool_result_blocks:
        if isinstance(tool_result.get("content"), list):
            new_content = []
            for content in tool_result.get("content", []):
                if isinstance(content, dict) and content.get("type") == "image":
                    if images_to_remove > 0:
                        images_to_remove -= 1
                        continue
                new_content.append(content)
            tool_result["content"] = new_content


def _response_to_params(
    response: BetaMessage,
) -> list[BetaContentBlockParam]:
    res: list[BetaContentBlockParam] = []
    for block in response.content:
        if isinstance(block, BetaTextBlock):
            if block.text:
                res.append(BetaTextBlockParam(type="text", text=block.text))
            elif getattr(block, "type", None) == "thinking":
                # Handle thinking blocks - include signature field
                thinking_block = {
                    "type": "thinking",
                    "thinking": getattr(block, "thinking", None),
                }
                if hasattr(block, "signature"):
                    thinking_block["signature"] = getattr(block, "signature", None)
                res.append(cast(BetaContentBlockParam, thinking_block))
        else:
            # Handle tool use blocks normally
            res.append(cast(BetaToolUseBlockParam, block.model_dump()))
    return res


def _inject_prompt_caching(
    messages: list[BetaMessageParam],
):
    """
    Set cache breakpoints for the 3 most recent turns
    one cache breakpoint is left for tools/system prompt, to be shared across sessions
    """

    breakpoints_remaining = 3
    for message in reversed(messages):
        if message["role"] == "user" and isinstance(
            content := message["content"], list
        ):
            if breakpoints_remaining:
                breakpoints_remaining -= 1
                # Use type ignore to bypass TypedDict check until SDK types are updated
                content[-1]["cache_control"] = BetaCacheControlEphemeralParam(  # type: ignore
                    {"type": "ephemeral"}
                )
            else:
                content[-1].pop("cache_control", None)
                # we'll only every have one extra turn per loop
                break


def _make_api_tool_result(
    result: ToolResult, tool_use_id: str
) -> BetaToolResultBlockParam:
    """Convert an agent ToolResult to an API ToolResultBlockParam."""
    tool_result_content: list[BetaTextBlockParam | BetaImageBlockParam] | str = []
    is_error = False
    if result.error:
        is_error = True
        tool_result_content = _maybe_prepend_system_tool_result(result, result.error)
    else:
        if result.output:
            tool_result_content.append(
                {
                    "type": "text",
                    "text": _maybe_prepend_system_tool_result(result, result.output),
                }
            )
        if result.base64_image:
            tool_result_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": result.base64_image,
                    },
                }
            )
    return {
        "type": "tool_result",
        "content": tool_result_content,
        "tool_use_id": tool_use_id,
        "is_error": is_error,
    }


def _maybe_prepend_system_tool_result(result: ToolResult, result_text: str):
    if result.system:
        result_text = f"<system>{result.system}</system>\n{result_text}"
    return result_text