#!/bin/bash
# 雪峰Agent 服务器一键部署脚本
# 在全新的 Ubuntu/Debian 服务器上运行：bash install.sh
set -e

APP_DIR="/opt/xuefeng-agent"
PORT=5000

echo "=== 雪峰Agent 部署开始 ==="

# 1. 系统更新 + Python
echo "[1/8] 安装 Python3 和依赖..."
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

# 2. 创建应用目录
echo "[2/8] 部署应用文件..."
mkdir -p $APP_DIR
mkdir -p $APP_DIR/logs
# 假设项目文件已在当前目录，复制过去
cp -r ../GraphRAG $APP_DIR/
cp -r ../langgraph $APP_DIR/
cp -r ../data $APP_DIR/
cp ../log_setup.py $APP_DIR/
cp ../.gitignore $APP_DIR/
cp ../.env.example $APP_DIR/

# 3. 解压数据库
echo "[3/8] 解压数据库..."
cd $APP_DIR/GraphRAG
python3 -c "
import gzip, shutil, os
gz = 'admission_clean.db.gz'
db = 'admission_clean.db'
if os.path.exists(gz) and not os.path.exists(db):
    with gzip.open(gz, 'rb') as f_in, open(db, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    print('数据库解压完成')
else:
    print('数据库已存在或gz文件不存在')
"

# 4. 创建虚拟环境
echo "[4/8] 创建 Python 虚拟环境..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate

# 5. 安装 Python 依赖
echo "[5/8] 安装 Python 包..."
pip install --upgrade pip
pip install -r $APP_DIR/GraphRAG/requirements-engine.txt
pip install -r $APP_DIR/langgraph/requirements.txt

# 6. 创建环境变量文件
echo "[6/8] 创建环境变量文件..."
cat > $APP_DIR/.env << 'EOF'
# 环境变量配置
# 请在此处填写实际的 API 密钥
DEEPSEEK_API_KEY=your_deepseek_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
EOF

# 7. 创建 systemd 服务（开机自启）
echo "[7/8] 配置开机自启..."
cat > /etc/systemd/system/xuefeng-agent.service << 'EOF'
[Unit]
Description=雪峰Agent Server
After=network.target

[Service]
Type=simple
User=xuefeng
Group=xuefeng
WorkingDirectory=/opt/xuefeng-agent/GraphRAG
ExecStart=/opt/xuefeng-agent/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 --timeout 120 --access-logfile /opt/xuefeng-agent/logs/access.log --error-logfile /opt/xuefeng-agent/logs/error.log api_server:app
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/xuefeng-agent/.env

[Install]
WantedBy=multi-user.target
EOF

# 8. 创建专用用户
echo "[8/8] 创建专用用户..."
if ! id -u xuefeng >/dev/null 2>&1; then
    useradd -r -s /bin/false xuefeng
    echo "用户 xuefeng 创建完成"
else
    echo "用户 xuefeng 已存在"
fi

# 设置目录权限
chown -R xuefeng:xuefeng $APP_DIR

# 启动服务
systemctl daemon-reload
systemctl enable xuefeng-agent
systemctl start xuefeng-agent

# 配置 nginx
echo "配置 nginx..."
cat > /etc/nginx/sites-available/xuefeng-agent << 'EOF'
server {
    listen 80;
    server_name YOUR_DOMAIN.COM;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/xuefeng-agent /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=== 部署完成 ==="
echo ""
echo "下一步："
echo "1. 编辑 /opt/xuefeng-agent/.env 文件，填入实际的 API 密钥"
echo "   参考 /opt/xuefeng-agent/.env.example"
echo ""
echo "2. 编辑 /etc/nginx/sites-available/xuefeng-agent"
echo "   把 YOUR_DOMAIN.COM 改成你的域名"
echo ""
echo "3. 申请 HTTPS 证书："
echo "   certbot --nginx -d YOUR_DOMAIN.COM"
echo ""
echo "4. 配置定时清理（可选）："
echo "   crontab -e"
echo "   添加: 0 4 * * * cd /opt/xuefeng-agent && /opt/xuefeng-agent/venv/bin/python scripts/cleanup.py >> /opt/xuefeng-agent/logs/cleanup.log 2>&1"
echo ""
echo "5. 测试："
echo "   curl http://localhost:5000/api/health"
echo "   systemctl status xuefeng-agent"