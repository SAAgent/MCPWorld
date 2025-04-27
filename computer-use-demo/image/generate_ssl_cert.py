#!/usr/bin/env python3
import os
import subprocess


def generate_ssl_certificates():
    """生成SSL证书和私钥"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(current_dir, "key.pem")
    cert_path = os.path.join(current_dir, "server.pem")

    # 检查证书是否已存在
    if os.path.exists(key_path) and os.path.exists(cert_path):
        print("SSL证书已存在，如需重新生成，请先删除现有的key.pem和server.pem文件")
        return

    print("生成SSL证书...")
    
    # 生成私钥
    subprocess.run(
        ["openssl", "genrsa", "-out", key_path, "2048"],
        check=True
    )
    
    # 生成自签名证书
    # 设置证书信息
    subject = "/C=CN/ST=State/L=City/O=Organization/OU=Unit/CN=localhost"
    
    subprocess.run(
        [
            "openssl", "req", "-new", "-x509", 
            "-key", key_path, 
            "-out", cert_path, 
            "-days", "365",
            "-subj", subject
        ],
        check=True
    )
    
    print(f"SSL证书已生成: {cert_path}")
    print(f"SSL私钥已生成: {key_path}")
    print("\n注意：这是自签名证书，浏览器可能会显示安全警告。")


if __name__ == "__main__":
    generate_ssl_certificates() 