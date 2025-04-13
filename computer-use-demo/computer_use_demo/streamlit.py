"""
Entrypoint for streamlit, see https://docs.streamlit.io/
"""

import asyncio
import base64
import os
import subprocess
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from functools import partial
from pathlib import PosixPath
from typing import cast, get_args
import sys
import time
import threading

import httpx
import streamlit as st
from anthropic import RateLimitError
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
)
from streamlit.delta_generator import DeltaGenerator
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from computer_use_demo.loop import (
    APIProvider,
    sampling_loop,
)
from computer_use_demo.tools import ToolResult, ToolVersion

PROVIDER_TO_DEFAULT_MODEL_NAME: dict[APIProvider, str] = {
    APIProvider.ANTHROPIC: "claude-3-7-sonnet-20250219",
    APIProvider.BEDROCK: "anthropic.claude-3-5-sonnet-20241022-v2:0",
    APIProvider.VERTEX: "claude-3-5-sonnet-v2@20241022",
}


@dataclass(kw_only=True, frozen=True)
class ModelConfig:
    tool_version: ToolVersion
    max_output_tokens: int
    default_output_tokens: int
    has_thinking: bool = False


SONNET_3_5_NEW = ModelConfig(
    tool_version="computer_use_20241022",
    max_output_tokens=1024 * 8,
    default_output_tokens=1024 * 4,
)

SONNET_3_7 = ModelConfig(
    tool_version="computer_use_20250124",
    max_output_tokens=128_000,
    default_output_tokens=1024 * 16,
    has_thinking=True,
)

MODEL_TO_MODEL_CONF: dict[str, ModelConfig] = {
    "claude-3-7-sonnet-20250219": SONNET_3_7,
}

CONFIG_DIR = PosixPath("~/.anthropic").expanduser()
API_KEY_FILE = CONFIG_DIR / "api_key"
STREAMLIT_STYLE = """
<style>
    /* Highlight the stop button in red */
    button[kind=header] {
        background-color: rgb(255, 75, 75);
        border: 1px solid rgb(255, 75, 75);
        color: rgb(255, 255, 255);
    }
    button[kind=header]:hover {
        background-color: rgb(255, 51, 51);
    }
     /* Hide the streamlit deploy button */
    .stAppDeployButton {
        visibility: hidden;
    }
</style>
"""

WARNING_TEXT = "⚠️ Security Alert: Never provide access to sensitive accounts or data, as malicious web content can hijack Claude's behavior"
INTERRUPT_TEXT = "(user stopped or interrupted and wrote the following)"
INTERRUPT_TOOL_ERROR = "human stopped or interrupted tool execution"


class Sender(StrEnum):
    USER = "user"
    BOT = "assistant"
    TOOL = "tool"


def setup_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "api_key" not in st.session_state:
        # Try to load API key from file first, then environment
        st.session_state.api_key = load_from_storage("api_key") or os.getenv(
            "ANTHROPIC_API_KEY", ""
        )
    if "provider" not in st.session_state:
        st.session_state.provider = (
            os.getenv("API_PROVIDER", "anthropic") or APIProvider.ANTHROPIC
        )
    if "provider_radio" not in st.session_state:
        st.session_state.provider_radio = st.session_state.provider
    if "model" not in st.session_state:
        _reset_model()
    if "auth_validated" not in st.session_state:
        st.session_state.auth_validated = False
    if "responses" not in st.session_state:
        st.session_state.responses = {}
    if "tools" not in st.session_state:
        st.session_state.tools = {}
    if "only_n_most_recent_images" not in st.session_state:
        st.session_state.only_n_most_recent_images = 3
    if "custom_system_prompt" not in st.session_state:
        st.session_state.custom_system_prompt = load_from_storage("system_prompt") or ""
    if "hide_images" not in st.session_state:
        st.session_state.hide_images = False
    if "token_efficient_tools_beta" not in st.session_state:
        st.session_state.token_efficient_tools_beta = False
    if "in_sampling_loop" not in st.session_state:
        st.session_state.in_sampling_loop = False
    if "evaluator_task_id" not in st.session_state:
        st.session_state.evaluator_task_id = ""
    if "evaluator_enabled" not in st.session_state:
        st.session_state.evaluator_enabled = False
    if "evaluator_instance" not in st.session_state:
        st.session_state.evaluator_instance = None
    if "evaluator_app_path" not in st.session_state:
        st.session_state.evaluator_app_path = ""
    if "evaluator_started" not in st.session_state:
        st.session_state.evaluator_started = False
    if "evaluator_task_completed" not in st.session_state:
        st.session_state.evaluator_task_completed = False
    if "evaluator_task_result" not in st.session_state:
        st.session_state.evaluator_task_result = None
    if "evaluator_metrics" not in st.session_state:
        st.session_state.evaluator_metrics = {}
    if "evaluator_event_type" not in st.session_state:
        st.session_state.evaluator_event_type = None
    if "evaluator_last_update" not in st.session_state:
        st.session_state.evaluator_last_update = 0


def _reset_model():
    st.session_state.model = PROVIDER_TO_DEFAULT_MODEL_NAME[
        cast(APIProvider, st.session_state.provider)
    ]
    _reset_model_conf()


def _reset_model_conf():
    model_conf = (
        SONNET_3_7
        if "3-7" in st.session_state.model
        else MODEL_TO_MODEL_CONF.get(st.session_state.model, SONNET_3_5_NEW)
    )
    st.session_state.tool_version = model_conf.tool_version
    st.session_state.has_thinking = model_conf.has_thinking
    st.session_state.output_tokens = model_conf.default_output_tokens
    st.session_state.max_output_tokens = model_conf.max_output_tokens
    st.session_state.thinking_budget = int(model_conf.default_output_tokens / 2)


async def main():
    """Render loop for streamlit"""
    setup_state()

    st.markdown(STREAMLIT_STYLE, unsafe_allow_html=True)

    st.title("Claude Computer Use Demo")

    if not os.getenv("HIDE_WARNING", False):
        st.warning(WARNING_TEXT)

    with st.sidebar:

        def _reset_api_provider():
            if st.session_state.provider_radio != st.session_state.provider:
                _reset_model()
                st.session_state.provider = st.session_state.provider_radio
                st.session_state.auth_validated = False

        provider_options = [option.value for option in APIProvider]
        st.radio(
            "API Provider",
            options=provider_options,
            key="provider_radio",
            format_func=lambda x: x.title(),
            on_change=_reset_api_provider,
        )

        st.text_input("Model", key="model", on_change=_reset_model_conf)

        if st.session_state.provider == APIProvider.ANTHROPIC:
            st.text_input(
                "Anthropic API Key",
                type="password",
                key="api_key",
                on_change=lambda: save_to_storage("api_key", st.session_state.api_key),
            )

        st.number_input(
            "Only send N most recent images",
            min_value=0,
            key="only_n_most_recent_images",
            help="To decrease the total tokens sent, remove older screenshots from the conversation",
        )
        st.text_area(
            "Custom System Prompt Suffix",
            key="custom_system_prompt",
            help="Additional instructions to append to the system prompt. see computer_use_demo/loop.py for the base system prompt.",
            on_change=lambda: save_to_storage(
                "system_prompt", st.session_state.custom_system_prompt
            ),
        )
        st.checkbox("Hide screenshots", key="hide_images")
        st.checkbox(
            "Enable token-efficient tools beta", key="token_efficient_tools_beta"
        )
        versions = get_args(ToolVersion)
        st.radio(
            "Tool Versions",
            key="tool_versions",
            options=versions,
            index=versions.index(st.session_state.tool_version),
        )

        st.number_input("Max Output Tokens", key="output_tokens", step=1)

        st.checkbox("Thinking Enabled", key="thinking", value=False)
        st.number_input(
            "Thinking Budget",
            key="thinking_budget",
            max_value=st.session_state.max_output_tokens,
            step=1,
            disabled=not st.session_state.thinking,
        )

        st.divider()  # 添加分隔线
        st.subheader("Evaluator Settings")

        # 启用/禁用评估器的复选框
        evaluator_enabled = st.checkbox("Enable Evaluator", key="evaluator_enabled")

        # 只有在评估器启用时才显示任务ID和应用路径输入框
        if evaluator_enabled:
            st.text_input(
                "Task ID (format: category/id)",
                key="evaluator_task_id",
                help="Enter task ID in format 'category/task_id', e.g. 'telegram/task01_search'",
            )
            
            st.text_input(
                "Application Path",
                key="evaluator_app_path",
                help="Full path to the application executable (e.g., /workspace/PC-Canary/apps/tdesktop/out/Debug/Telegram)",
            )
            
            # 添加启动/停止按钮
            col1, col2 = st.columns(2)
            with col1:
                start_button = st.button("Start Evaluator", 
                                        disabled=st.session_state.evaluator_started,
                                        type="primary" if not st.session_state.evaluator_started else "secondary")
            with col2:
                stop_button = st.button("Stop Evaluator", 
                                       disabled=not st.session_state.evaluator_started,
                                       type="primary" if st.session_state.evaluator_started else "secondary")
            
            # 显示当前状态
            if st.session_state.evaluator_started:
                st.success("Evaluator is running")
                if st.session_state.evaluator_instance:
                    task_id = f"{st.session_state.evaluator_instance.task_category}/{st.session_state.evaluator_instance.task_id}"
                    st.info(f"Monitoring task: {task_id}")
            else:
                st.info("Evaluator is not running")
            
            # 处理按钮点击
            if start_button:
                if not st.session_state.evaluator_task_id or "/" not in st.session_state.evaluator_task_id:
                    st.error("Please enter a valid task ID in format 'category/id'")
                else:
                    # 启动评估器
                    with st.spinner("Starting evaluator..."):
                        if initialize_evaluator():
                            # 注册回调函数，将处理回调并更新全局状态
                            st.session_state.evaluator_started = True
                            st.session_state.evaluator_task_completed = False
                            st.session_state.evaluator_task_result = None
                            st.info(f"Evaluator started for task: {st.session_state.evaluator_task_id}")
                            st.rerun()  # 重新运行以更新UI状态
            
            if stop_button and st.session_state.evaluator_instance:
                with st.spinner("Stopping evaluator..."):
                    stop_evaluator()
            
            if st.session_state.evaluator_instance:
                st.divider()
                st.subheader("Evaluator Status")
                
                # 检查是否有全局状态更新并同步到Streamlit
                # 显示最后更新时间
                last_update_time = time.strftime("%H:%M:%S", time.localtime(st.session_state.evaluator_last_update)) if st.session_state.evaluator_last_update > 0 else "未更新"
                st.empty().markdown(f"Last update: {last_update_time}")
                
                evaluator = st.session_state.evaluator_instance
                st.write(f"**任务:** {evaluator.instruction}")
                
                # 改进状态显示逻辑
                if st.session_state.evaluator_task_completed:
                    status = "已完成"
                elif not evaluator.is_running:
                    status = "已停止"
                else:
                    status = "运行中"
                st.write(f"**状态:** {status}")
                
                # 显示任务完成状态
                if st.session_state.evaluator_task_completed:
                    st.success(f"任务已完成: {st.session_state.evaluator_task_result}")
                    
                    # 显示评估指标
                    if st.session_state.evaluator_metrics:
                        st.subheader("评估指标")
                        st.json(st.session_state.evaluator_metrics)
                
                # 添加刷新按钮，手动检查状态
                if st.button("刷新状态", key="refresh_evaluator_status"):
                    # 直接从evaluator获取最新指标
                    if evaluator and hasattr(evaluator, 'metrics'):
                        st.session_state.evaluator_metrics = evaluator.metrics.copy()
                        st.session_state.evaluator_last_update = time.time()
                        st.rerun()

        if st.button("Reset", type="primary"):
            with st.spinner("Resetting..."):
                st.session_state.clear()
                setup_state()

                subprocess.run("pkill Xvfb; pkill tint2", shell=True)  # noqa: ASYNC221
                await asyncio.sleep(1)
                subprocess.run("./start_all.sh", shell=True)  # noqa: ASYNC221

    if not st.session_state.auth_validated:
        if auth_error := validate_auth(
            st.session_state.provider, st.session_state.api_key
        ):
            st.warning(f"Please resolve the following auth issue:\n\n{auth_error}")
            return
        else:
            st.session_state.auth_validated = True

    chat, http_logs = st.tabs(["Chat", "HTTP Exchange Logs"])
    new_message = st.chat_input(
        "Type a message to send to Claude to control the computer..."
    )

    with chat:
        # render past chats
        for message in st.session_state.messages:
            if isinstance(message["content"], str):
                _render_message(message["role"], message["content"])
            elif isinstance(message["content"], list):
                for block in message["content"]:
                    # the tool result we send back to the Anthropic API isn't sufficient to render all details,
                    # so we store the tool use responses
                    if isinstance(block, dict) and block["type"] == "tool_result":
                        _render_message(
                            Sender.TOOL, st.session_state.tools[block["tool_use_id"]]
                        )
                    else:
                        _render_message(
                            message["role"],
                            cast(BetaContentBlockParam | ToolResult, block),
                        )

        # render past http exchanges
        for identity, (request, response) in st.session_state.responses.items():
            _render_api_response(request, response, identity, http_logs)

        # render past chats
        if new_message:
            st.session_state.messages.append(
                {
                    "role": Sender.USER,
                    "content": [
                        *maybe_add_interruption_blocks(),
                        BetaTextBlockParam(type="text", text=new_message),
                    ],
                }
            )
            _render_message(Sender.USER, new_message)

        try:
            most_recent_message = st.session_state["messages"][-1]
        except IndexError:
            return

        if most_recent_message["role"] is not Sender.USER:
            # we don't have a user message to respond to, exit early
            return

        with track_sampling_loop():
            # run the agent sampling loop with the newest message
            st.session_state.messages = await sampling_loop(
                system_prompt_suffix=st.session_state.custom_system_prompt,
                model=st.session_state.model,
                provider=st.session_state.provider,
                messages=st.session_state.messages,
                output_callback=partial(_render_message, Sender.BOT),
                tool_output_callback=partial(
                    _tool_output_callback, tool_state=st.session_state.tools
                ),
                api_response_callback=partial(
                    _api_response_callback,
                    tab=http_logs,
                    response_state=st.session_state.responses,
                ),
                api_key=st.session_state.api_key,
                only_n_most_recent_images=st.session_state.only_n_most_recent_images,
                tool_version=st.session_state.tool_version,
                max_tokens=st.session_state.output_tokens,
                thinking_budget=st.session_state.thinking_budget
                if st.session_state.thinking
                else None,
                token_efficient_tools_beta=st.session_state.token_efficient_tools_beta,
            )


def maybe_add_interruption_blocks():
    if not st.session_state.in_sampling_loop:
        return []
    # If this function is called while we're in the sampling loop, we can assume that the previous sampling loop was interrupted
    # and we should annotate the conversation with additional context for the model and heal any incomplete tool use calls
    result = []
    last_message = st.session_state.messages[-1]
    previous_tool_use_ids = [
        block["id"] for block in last_message["content"] if block["type"] == "tool_use"
    ]
    for tool_use_id in previous_tool_use_ids:
        st.session_state.tools[tool_use_id] = ToolResult(error=INTERRUPT_TOOL_ERROR)
        result.append(
            BetaToolResultBlockParam(
                tool_use_id=tool_use_id,
                type="tool_result",
                content=INTERRUPT_TOOL_ERROR,
                is_error=True,
            )
        )
    result.append(BetaTextBlockParam(type="text", text=INTERRUPT_TEXT))
    return result


@contextmanager
def track_sampling_loop():
    st.session_state.in_sampling_loop = True
    yield
    st.session_state.in_sampling_loop = False


def validate_auth(provider: APIProvider, api_key: str | None):
    if provider == APIProvider.ANTHROPIC:
        if not api_key:
            return "Enter your Anthropic API key in the sidebar to continue."
    if provider == APIProvider.BEDROCK:
        import boto3

        if not boto3.Session().get_credentials():
            return "You must have AWS credentials set up to use the Bedrock API."
    if provider == APIProvider.VERTEX:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError

        if not os.environ.get("CLOUD_ML_REGION"):
            return "Set the CLOUD_ML_REGION environment variable to use the Vertex API."
        try:
            google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except DefaultCredentialsError:
            return "Your google cloud credentials are not set up correctly."


def load_from_storage(filename: str) -> str | None:
    """Load data from a file in the storage directory."""
    try:
        file_path = CONFIG_DIR / filename
        if file_path.exists():
            data = file_path.read_text().strip()
            if data:
                return data
    except Exception as e:
        st.write(f"Debug: Error loading {filename}: {e}")
    return None


def save_to_storage(filename: str, data: str) -> None:
    """Save data to a file in the storage directory."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        file_path = CONFIG_DIR / filename
        file_path.write_text(data)
        # Ensure only user can read/write the file
        file_path.chmod(0o600)
    except Exception as e:
        st.write(f"Debug: Error saving {filename}: {e}")


def _api_response_callback(
    request: httpx.Request,
    response: httpx.Response | object | None,
    error: Exception | None,
    tab: DeltaGenerator,
    response_state: dict[str, tuple[httpx.Request, httpx.Response | object | None]],
):
    """
    Handle an API response by storing it to state and rendering it.
    """
    response_id = datetime.now().isoformat()
    response_state[response_id] = (request, response)
    if error:
        _render_error(error)
    _render_api_response(request, response, response_id, tab)


def _tool_output_callback(
    tool_output: ToolResult, tool_id: str, tool_state: dict[str, ToolResult]
):
    """Handle a tool output by storing it to state and rendering it."""
    tool_state[tool_id] = tool_output
    _render_message(Sender.TOOL, tool_output)


def _render_api_response(
    request: httpx.Request,
    response: httpx.Response | object | None,
    response_id: str,
    tab: DeltaGenerator,
):
    """Render an API response to a streamlit tab"""
    with tab:
        with st.expander(f"Request/Response ({response_id})"):
            newline = "\n\n"
            st.markdown(
                f"`{request.method} {request.url}`{newline}{newline.join(f'`{k}: {v}`' for k, v in request.headers.items())}"
            )
            st.json(request.read().decode())
            st.markdown("---")
            if isinstance(response, httpx.Response):
                st.markdown(
                    f"`{response.status_code}`{newline}{newline.join(f'`{k}: {v}`' for k, v in response.headers.items())}"
                )
                st.json(response.text)
            else:
                st.write(response)


def _render_error(error: Exception):
    if isinstance(error, RateLimitError):
        body = "You have been rate limited."
        if retry_after := error.response.headers.get("retry-after"):
            body += f" **Retry after {str(timedelta(seconds=int(retry_after)))} (HH:MM:SS).** See our API [documentation](https://docs.anthropic.com/en/api/rate-limits) for more details."
        body += f"\n\n{error.message}"
    else:
        body = str(error)
        body += "\n\n**Traceback:**"
        lines = "\n".join(traceback.format_exception(error))
        body += f"\n\n```{lines}```"
    save_to_storage(f"error_{datetime.now().timestamp()}.md", body)
    st.error(f"**{error.__class__.__name__}**\n\n{body}", icon=":material/error:")


def _render_message(
    sender: Sender,
    message: str | BetaContentBlockParam | ToolResult,
):
    """Convert input from the user or output from the agent to a streamlit message."""
    # streamlit's hotreloading breaks isinstance checks, so we need to check for class names
    is_tool_result = not isinstance(message, str | dict)
    if not message or (
        is_tool_result
        and st.session_state.hide_images
        and not hasattr(message, "error")
        and not hasattr(message, "output")
    ):
        return
    with st.chat_message(sender):
        if is_tool_result:
            message = cast(ToolResult, message)
            if message.output:
                if message.__class__.__name__ == "CLIResult":
                    st.code(message.output)
                else:
                    st.markdown(message.output)
            if message.error:
                st.error(message.error)
            if message.base64_image and not st.session_state.hide_images:
                st.image(base64.b64decode(message.base64_image))
        elif isinstance(message, dict):
            if message["type"] == "text":
                st.write(message["text"])
            elif message["type"] == "thinking":
                thinking_content = message.get("thinking", "")
                st.markdown(f"[Thinking]\n\n{thinking_content}")
            elif message["type"] == "tool_use":
                st.code(f'Tool Use: {message["name"]}\nInput: {message["input"]}')
            else:
                # only expected return types are text and tool_use
                raise Exception(f'Unexpected response type {message["type"]}')
        else:
            st.markdown(message)


def initialize_evaluator():
    """初始化或更新评估器实例，返回是否成功"""
    if not st.session_state.evaluator_enabled or not st.session_state.evaluator_task_id:
        st.session_state.evaluator_instance = None
        return False
    
    # 获取应用路径
    app_path = st.session_state.evaluator_app_path
    # 解析任务ID
    try:
        category, task_id = st.session_state.evaluator_task_id.split("/", 1)
        task = {
            "category": category,
            "id": task_id
        }
        
        # 导入PC-Canary的评估器
        FILE_ROOT = os.path.dirname(os.path.abspath(__file__))
        PROJECT_ROOT = os.path.dirname(os.path.dirname(FILE_ROOT))
        EVALUATOR_PATH = os.path.join(PROJECT_ROOT, "PC-Canary")
        if os.path.exists(EVALUATOR_PATH) and EVALUATOR_PATH not in sys.path:
            sys.path.append(EVALUATOR_PATH)
        from evaluator.core.base_evaluator import BaseEvaluator
        
        # 创建评估器实例
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        
        # 如果已有实例且任务ID不同，先停止现有实例
        if (st.session_state.evaluator_instance and 
            (st.session_state.evaluator_instance.task_id != task_id or
             getattr(st.session_state.evaluator_instance, 'app_path', None) != app_path)):
            st.session_state.evaluator_instance.stop()
            if hasattr(st.session_state.evaluator_instance, 'stop_app'):
                st.session_state.evaluator_instance.stop_app()
            st.session_state.evaluator_instance = None
        
        # 只有在实例不存在时才创建新实例
        if not st.session_state.evaluator_instance:
            # 创建新实例
            st.session_state.evaluator_instance = BaseEvaluator(
                task=task,
                log_dir=log_dir,
                app_path=app_path
            )
            
            # 注册回调函数
            st.session_state.evaluator_instance.register_completion_callback(handle_evaluator_event)
            # !!! 重要，st 的实现与多线程的兼容不佳，根据官方文档需要手动配置上下文
            # 保存当前上下文
            current_ctx = get_script_run_ctx()
            
            # 保存原始的 Thread.__init__
            original_thread_init = threading.Thread.__init__

            # 创建新的 __init__ 函数
            def patched_thread_init(self, *args, **kwargs):
                original_thread_init(self, *args, **kwargs)
                add_script_run_ctx(self, current_ctx)
            
            # 应用补丁
            threading.Thread.__init__ = patched_thread_init
            
            try:
                # 启动评估器
                success = st.session_state.evaluator_instance.start()
            finally:
                # 恢复原始 __init__
                threading.Thread.__init__ = original_thread_init
                
            if success:
                st.success(f"Evaluator initialized for task: {category}/{task_id}")
                if app_path:
                    st.info(f"Monitoring application: {app_path}")
                return True
            else:
                st.error("Failed to start evaluator")
                st.session_state.evaluator_instance = None
                return False
        return True  # 已有实例且无需重启
        
    except Exception as e:
        st.error(f"Failed to initialize evaluator: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
        st.session_state.evaluator_instance = None
        return False


def stop_evaluator():
    """停止评估器并停止轮询线程"""
    if st.session_state.evaluator_instance:
        st.session_state.evaluator_instance.stop()
        if hasattr(st.session_state.evaluator_instance, 'stop_app'):
            st.session_state.evaluator_instance.stop_app()
        st.session_state.evaluator_instance = None
        st.session_state.evaluator_started = False
        st.info("Evaluator stopped successfully")
        st.rerun()


# 调整handle_evaluator_event函数，保持与全局状态的配合
FILE_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(FILE_ROOT))
EVALUATOR_PATH = os.path.join(PROJECT_ROOT, "PC-Canary")
if os.path.exists(EVALUATOR_PATH) and EVALUATOR_PATH not in sys.path:
    sys.path.append(EVALUATOR_PATH)
from evaluator.core.base_evaluator import EventData, EventType, BaseEvaluator
    
def handle_evaluator_event(event_data: EventData, evaluator: BaseEvaluator):
    """处理评估器事件，直接更新Streamlit会话状态"""
    print(f"处理事件: {event_data.event_type} - {event_data.message}")
    
    # 直接更新session_state
    st.session_state.evaluator_event_type = event_data.event_type
    st.session_state.evaluator_last_update = time.time()
    
    if event_data.event_type == EventType.TASK_COMPLETED:
        st.session_state.evaluator_task_completed = True
        st.session_state.evaluator_task_result = event_data.message
        print(f"任务完成: {event_data.message}")
        # 更新指标数据
        if hasattr(event_data, 'data') and event_data.data:
            st.session_state.evaluator_metrics = event_data.data.get('metrics', {})
    
    elif event_data.event_type == EventType.TASK_ERROR:
        st.session_state.evaluator_task_completed = False
        st.session_state.evaluator_task_result = event_data.message
        print(f"任务错误: {event_data.message}")
    
    elif event_data.event_type == EventType.EVALUATOR_STOPPED:
        st.session_state.evaluator_task_completed = False
        st.session_state.evaluator_task_result = event_data.message
        print(f"评估器停止: {event_data.message}")
    
    # 尝试触发Streamlit重新渲染
    try:
        evaluator = st.session_state.evaluator_instance
        if evaluator and hasattr(evaluator, 'metrics'):
            st.session_state.evaluator_metrics = evaluator.metrics.copy()
            st.session_state.evaluator_last_update = time.time()
            st.rerun()

    except Exception as e:
        print(f"无法直接触发重新渲染: {e}")


if __name__ == "__main__":
    asyncio.run(main())
