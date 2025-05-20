# MCPWorld: Hardware-Accelerated Environment Setup

This document outlines the specific steps to configure and run MCPWorld with hardware acceleration. It assumes you are familiar with the general MCPWorld setup. For a full overview of MCPWorld, its features, and basic installation (including initial cloning and submodule setup not detailed here), please refer to the `README.md` in main branch.

The primary difference for hardware acceleration lies in ensuring the Docker container has access to GPU resources.

## Prerequisites for Hardware Acceleration

*   **Host Machine:**
    *   NVIDIA GPU
    *   NVIDIA drivers installed.
    *   Docker installed.
    *   NVIDIA Container Toolkit installed to enable GPU access for Docker containers.
*   **Project Files:**
    *   Ensure you are on the git branch of MCPWorld that supports hardware acceleration.
    *   The `PC-Canary` submodule should also be initialized in `gpu_accel` branch, as its Dockerfile is used to build the VNC desktop environment with hardware acceleration support.

## Building the Hardware-Accelerated Docker Image

The Docker image for the VNC desktop environment, which includes support for hardware acceleration, is built using the Dockerfile provided within the `PC-Canary` submodule.

1.  **Ensure Submodules are Updated:**
    If you haven't already, or to ensure you have the correct version for this branch:
    ```bash
    git submodule update --init PC-Canary
    ```

2.  **Build the Docker Image:**
    Refer to the `README.md` within the `PC-Canary` submodule for specific instructions on building its Docker image. This image should be built from the version of `PC-Canary` on this hardware acceleration branch, as it's configured to include necessary GPU libraries (e.g., CUDA, cuDNN).
    *Example (adapt as per `PC-Canary`'s instructions):*
    ```bash
    # cd PC-Canary 
    # docker build -t mcpworld-hw-accel .
    # cd ..
    ```

## Running the Docker Container with GPU Access

To enable hardware acceleration, you must explicitly grant the Docker container access to your host's GPU(s) when running it.

```bash
# Example:
docker run -it --rm \
  --gpus all \
  # Or for older nvidia-docker versions, you might use --runtime=nvidia
  # Add your necessary port mappings, e.g.:
  -p 8083:8083 \
  -p 8501:8501 \
  -p 5906:5906 \ # Map VNC port (e.g., if VNC is on display :6 inside container)
  # Add any volume mounts if needed
  # -v /path/on/host:/path/in/container \
  your-built-image-name # e.g., mcpworld-hw-accel
```
*   Replace `your-built-image-name` with the actual name of the Docker image you built.
*   Adjust port mappings as per your setup (the example maps common ports for the demo).

## Starting Services Inside the Container

Once the hardware-accelerated container is running and you have a shell inside it (e.g., via `docker exec -it <container_name_or_id> /bin/bash` or if `docker run` launched a shell):

1.  **Install Demo Dependencies (if not pre-built into the image):**
    ```bash
    # (Optional) Activate conda environment if used:
    # conda activate agent-env
    pip install -r computer-use-demo/computer_use_demo/requirements.txt
    ```

2.  **Start VNC Server:**
    This provides the graphical desktop environment where applications can leverage GPU acceleration.
    ```bash
    # Example: Start VNC on display :6 with 1024x768 resolution
    vncserver -geometry 1024x768 :6
    ```
    *(You will be prompted to set a VNC password on the first run).*

3.  **Start Main Page HTTP Server:**
    This server provides a web-based entry point to access VNC and the Streamlit UI.
    ```bash
    # Generate SSL certificate (needed for some noVNC setups, run once if needed)
    python computer-use-demo/image/generate_ssl_cert.py

    # Start the HTTP server (e.g., on port 8083)
    python computer-use-demo/image/http_server.py > /tmp/server_logs.txt 2>&1 &
    ```

4.  **Start Agent Demo & Evaluator UI (Streamlit App):**
    ```bash
    cd computer-use-demo
    STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py
    # To run in background:
    # STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py > /tmp/streamlit_logs.txt 2>&1 &
    cd ..
    ```

5.  **Accessing the Demo:**
    After all services are running, open your web browser and navigate to `https://<YOUR_HOST_IP_OR_LOCALHOST>:8083`.
    *   If running Docker on a remote machine, use its IP. If local, `localhost` or `127.0.0.1` should work.
    *   The page served by `http_server.py` should provide links to the VNC session and the Streamlit UI.

    **(Known Issue Reminder):** Ensure the VNC address in `computer-use-demo/image/static_content/index.html` is correctly configured to point to your VNC server's accessible IP and port, especially if not using `localhost`.

---

This setup ensures that applications and agents running within the MCPWorld environment can utilize available GPU resources, which is beneficial for tasks involving machine learning model inference or other computationally intensive graphical operations.