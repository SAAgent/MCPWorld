import os
import socket
import ssl
from http.server import HTTPServer, SimpleHTTPRequestHandler


class HTTPServerV6(HTTPServer):
    address_family = socket.AF_INET6


def run_server():
    os.chdir(os.path.dirname(__file__) + "/static_content")
    server_address = ("::", 8083)
    httpd = HTTPServerV6(server_address, SimpleHTTPRequestHandler)
    
    # 配置SSL
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # 检查证书文件是否存在，如果不存在，则创建自签名证书
    cert_path = os.path.join(os.path.dirname(__file__), "server.pem")
    key_path = os.path.join(os.path.dirname(__file__), "key.pem")
    
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        # 如果证书不存在，输出提示信息
        print("SSL证书不存在，请先创建证书文件：")
        print("生成私钥：openssl genrsa -out key.pem 2048")
        print("生成证书：openssl req -new -x509 -key key.pem -out server.pem -days 365")
        print("然后将证书文件放在与此脚本相同的目录中")
        return
    
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
    print("Starting HTTPS server on port 8083...")  # noqa: T201
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
