# Agent Runtime 运行环境——支持硬件加速

## 配置并启动支持硬件加速的 Agent Demo

需要克隆本仓库，并先配置好 submodule PC-Canary，以搭建 VNC 桌面环境

请阅读 PC-Canary 的 README 文档，通过 PC-Canary 下的 Dockerfile 构建 VNC 桌面环境
```bash
git submodule update --init PC-Canary
```

进入容器后执行如下配置以启动环境，如下配置假定
1. 在:6 处启动vncserver，vnc 的分辨率定在 1024x768，这是 Claude 官方文档中推荐的分辨率
2. 在 8083 端口启动主网页的服务器，如果是第一次启动，需要生成 https 证书，新的 VNC 桌面要求 https 协议
3. 在 8501 端口启动 streamlit 服务

```bash
# 1. setup conda environment
# conda activate agent-env
pip install -r computer-use-demo/computer_use_demo/requirements.txt
# 2. start vnc server 
vncserver -geometry  1024x768 :6

# 3. start main page server
python computer-use-demo/image/generate_ssl_cert.py

python computer-use-demo/image/http_server.py    > /tmp/server_logs.txt 2>&1 &

# 4. start streamlit server 
cd computer-use-demo
STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py
```
随后访问`https://your-ip:8083`即可进入主页环境中

（建议）在桌面环境中把自动锁屏关掉

（已知问题）要注意`computer-use-demo/image/static_content/index.html` 中的 vnc 地址是否与实际 VNC 桌面的 IP 和端口一致，可能需要改成实际 ip 而非 localhost

## 配置并启动 Evaluator Demo
目前的实现中 streamlit.py 已经集成了 evaluator 的服务，因此只需要启动 streamlit 服务即可。

当前的实现能在网页中同时运行 Agent 和 Evaluator，在运行的过程中配置单个任务并测试 Agent 是否能够完成任务。

但是实际使用 Evaluator 需要配置好用于评测的 app。在 PC-Canary 的 README 中描述了一个示例：其将 tdesktop 客户端的代码主仓库作为 submodule 引入，将应用客户端编译以后接入容器环境中评测。

在完成上述的配置，接入可执行应用如 tdesktop 后，就可以在实际 demo 网页中使用 evaluator。

目前还需要手动输入单个任务的instruction，点击发送给云端 LLM 后执行任务。此外由于 streamlit 库的一些限制，目前需要完成任务后手动刷新一下才能让结果在网页上显示。