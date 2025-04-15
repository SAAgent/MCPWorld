# Agent Runtime 运行环境

## 配置并启动 Agent Demo

需要克隆本仓库，并先配置好 submodule PC-Canary，以搭建 VNC 桌面环境，通过PC-Canary下的 Dockerfile 构建容器环境
```bash
git submodule update --init PC-Canary
```

进入容器后执行如下配置以启动环境，如下配置假定
1. 在:4（5904端口） 处启动vncserver，vnc 的分辨率定在 1024x768，这是 Claude 官方文档中推荐的分辨率
2. 在 6080 端口启动 noVNC 服务
3. 在 8501 端口启动 streamlit 服务
4. 在 8081 端口启动主网页的服务器

```bash
# 1. (optional) setup conda environment
# conda activate agent-env
pip install -r computer-use-demo/computer_use_demo/requirements.txt
pip install -r /workspace/PC-Canary/requirements.txt
# 2. start vnc server 

vncserver -xstartup /home/agent/.vnc/xstartup  -geometry  1024x768 :4

# 3. start noVNC service

/opt/noVNC/utils/novnc_proxy \
    --vnc 0.0.0.0:5904 \
    --listen 0.0.0.0:6080 \
    --web /opt/noVNC \
    > /tmp/novnc.log 2>&1 &

# 4. start main page server 

python computer-use-demo/image/http_server.py    > /tmp/server_logs.txt 2>&1 &

# 5. start streamlit server 
cd computer-use-demo
STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py
```
随后访问`http://your-ip:8081`即可进入主页环境中

（建议）进入 xfce4 环境后把 Firefox 加入底层任务栏中，Claude 的 prompt 中涉及了这块的配置

（建议）在桌面环境中把自动锁屏关掉

（已知问题） localhost IP 在如 noVNC 服务中不一定总是可用，必要时可以手动把 0.0.0.0 之流改成容器外 IP

## 配置并启动 Evaluator Demo
目前的实现中 streamlit.py 已经集成了 evaluator 的服务，因此只需要启动 streamlit 服务即可。

当前的实现能在网页中同时运行 Agent 和 Evaluator，在运行的过程中配置单个任务并测试 Agent 是否能够完成任务。

但目前还需要手动输入单个任务的instruction，点击发送给云端 LLM 后执行任务。