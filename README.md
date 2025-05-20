# MCPWorld: A Multi-Modal Test Platform for Computer-Using Agents (CUA)

![License](https://img.shields.io/badge/license-MIT-blue.svg) ![Docker](https://img.shields.io/badge/Docker-Supported-green.svg)

MCPWorld is an open-source benchmarking framework designed for evaluating **Computer-Using Agents (CUAs)**. It supports agents that interact with software applications via **GUI**, **API (Model Context Protocol – MCP)**, or **Hybrid** methods.

---

## 🚀 Key Features

* **Comprehensive Task Suite**

  * \~170 tasks across 10+ open-source applications (VSCode, OBS, Zotero, etc.).

* **GUI, API, and Hybrid Interaction**

  * Integrated MCP support enables robust mixed-mode control, letting agents fall back to GUI when APIs are unavailable.

* **White-Box Evaluation**

  * Built-in evaluators inspect internal app signals or outputs for precise, reproducible task verification.

* **Cross-Platform via Docker**

  * Containerized environments ensure consistent setups on Linux, macOS, and Windows.

* **Extensible Framework**

  * Easily add new tasks, applications, or custom agents via clear folder structure and interfaces.

---

## 📦 Installation

### Prerequisites

* Docker
* (Optional) VS Code + DevContainers extension

### Quick Setup

```bash
git clone https://github.com/SAAgent/MCPWorld.git
cd MCPWorld
git submodule update --init PC-Canary
```

Then open the folder in VS Code and select **Reopen in Container**, or manually build the image according to the Dockerfile provided by PC-Canary.

---

## 🚩 Quickstart

### 🚀 Running the Interactive Agent Demo with Evaluation

These instructions assume you are running commands inside the DevContainer.

1.  **Install Dependencies:**

    First, ensure all Python dependencies for the agent demo are installed:
    ```bash
    pip install -r computer-use-demo/computer_use_demo/requirements.txt
    ```

2.  **Start Required Services:**

    You'll need to start several services. It's recommended to run each in a separate terminal session within the container, or run them in the background.

    *   **VNC Server:** This provides the graphical desktop environment for the agent. The `xstartup` script configured in the Dockerfile will prepare an XFCE session.
        ```bash
        vncserver -xstartup ~/.vnc/xstartup -geometry 1024x768 :4
        ```
        This typically makes VNC available on port `5904`.

    *   **noVNC Proxy:** This allows you to access the VNC session via a web browser.
        ```bash
        /opt/noVNC/utils/novnc_proxy \
            --vnc localhost:5904 \
            --listen 0.0.0.0:6080 \
            --web /opt/noVNC > /tmp/novnc.log 2>&1 &
        ```
    *   **Main Page HTTP Server:** This server provides a unified entry point to access both VNC and the Streamlit UI.
        ```bash
        python computer-use-demo/image/http_server.py > /tmp/http_server.log 2>&1 &
        ```

    *   **Agent Demo & Evaluator UI (Streamlit App):** This application serves as the control panel for running tasks with the agent and viewing evaluation results.
        ```bash
        cd computer-use-demo
        STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py > /tmp/streamlit.log 2>&1 &
        ```

3.  **Accessing the Demo:**

    *   **Unified Interface:** Access the main entry page via your web browser at `http://localhost:8081`. This page should provide links to the VNC desktop and the Agent/Evaluator Streamlit UI.
    *   **VNC Desktop (Direct):** Access the agent's desktop environment directly via `http://localhost:6080`.
    *   **Agent & Evaluator UI (Direct):** Open `http://localhost:8501` directly to interact with the Streamlit application.

    <!-- (Ensure ports `8081`, `6080`, and `8501` are forwarded if you're accessing from outside the Docker host). -->

    Through the Streamlit UI (or by direct interaction if using the headless mode below), you can assign tasks to the agent. The agent will then interact with applications within the VNC desktop environment. The Evaluator will monitor and report on the agent's performance.

<!-- **Recommendations for VNC Environment:**
*   For a smoother experience, consider adding frequently used applications (e.g., Firefox) to the taskbar within the XFCE desktop environment.
*   Disable automatic screen locking in the XFCE power manager settings. -->

### 🧪 Headless Agent & Evaluator Execution (CLI-Only)

For scenarios where a UI is not needed or desired (e.g., automated batch testing), you can run the agent and evaluator directly from the command line using the `run_pure_computer_use_with_eval.py` script. This script handles the interaction loop and evaluation process without launching the Streamlit web interface.

**Prerequisites:**
*   Ensure the VNC server is running as described in the "Interactive Agent Demo" section if your tasks require GUI interaction. The VNC server provides the environment for the agent to operate in.
*   Ensure you have set your Anthropic API key, either via the `--api_key` argument or the `ANTHROPIC_API_KEY` environment variable.

**Example Command:**

```bash
python computer-use-demo/run_pure_computer_use_with_eval.py \
  --api_key <YOUR_ANTHROPIC_API_KEY> \
  --model claude-3-7-sonnet-20250219 \
  --task_id telegram/task01_search \
  --log_dir logs_computer_use_eval \
  --exec_mode mixed
```

<!-- **Key Parameters for `run_pure_computer_use_with_eval.py`:**
*   `--api_key`: Your Anthropic API key.
*   `--model`: The specific Anthropic model to use (e.g., `claude-3-opus-20240229`, `claude-3-sonnet-20240229`).
*   `--task_id`: The ID of the task from PC-Canary (e.g., `libreoffice/writer_create_document`, `gimp/crop_image`). This is a **required** argument.
*   `--log_dir`: Directory where evaluation logs and results will be saved.
*   `--max_turns`: Maximum number of conversational turns between the user (or initial instruction) and the agent.
*   `--timeout`: Overall timeout for the task execution in seconds.
*   `--exec_mode`: Agent's interaction mode (`mixed`, `gui`, or `api`).
*   `--app_path` (Optional): Path to a specific application if the task requires it and it's not discoverable by default.
*   `--tool_version` (Optional): Specify a particular version of tools if needed (defaults to `computer_use_20250124`).
*   `--system_prompt_suffix` (Optional): Additional text to append to the system prompt. -->

This script will output agent interactions and evaluation events directly to the console. Final results and detailed logs will be saved in the directory specified by `--log_dir`.

<!-- Run the full benchmark:

```bash
python scripts/run_benchmark.py \
  --agent gpt-4 \
  --mode hybrid \
  --output results/gpt4_hybrid.json
``` -->

---

## 📚 Documentation

* **Tasks**: See `PC-Canary/tests/tasks/` for JSON/JS/Python configs.
* **Agents**: Reference implementations in `computer-use-demo/`.
* **Extension**: Add new apps/tasks/agents as described in docs (Update in progress).
* **Evaluation**: White-box evaluators guarantee objective metrics.

---

<!-- ## 📖 Citation

```bibtex
@inproceedings{MCPWorld2025,
  title     = {MCPWorld: A Multi-Modal Test Platform for Computer-Using Agents},
  author    = {YourName and Author1 and Author2},
  booktitle = {NeurIPS 2025},
  year      = {2025}
}
``` -->

---

## 📝 License

Released under the MIT License.
