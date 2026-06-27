#!/bin/bash
# 验证部署配置是否正确
echo "=== 验证部署配置 ==="

# 检查端口一致性
echo "1. 检查端口配置..."
if grep -q "8765" deploy/install.sh deploy/nginx.conf deploy/README.md; then
    echo "❌ 发现旧端口 8765，请修复"
    grep -n "8765" deploy/install.sh deploy/nginx.conf deploy/README.md
else
    echo "✅ 端口配置正确（5000）"
fi

# 检查systemd服务配置
echo ""
echo "2. 检查systemd服务配置..."
if grep -q "User=root" deploy/xuefeng-agent.service; then
    echo "❌ 服务仍以root用户运行"
else
    echo "✅ 服务使用专用用户"
fi

if grep -q "server.py" deploy/xuefeng-agent.service; then
    echo "❌ 服务指向错误的入口文件"
else
    echo "✅ 服务入口配置正确"
fi

# 检查关键文件是否存在
echo ""
echo "3. 检查关键文件..."
if [ -f "GraphRAG/api_server.py" ]; then
    echo "✅ api_server.py 存在"
else
    echo "❌ api_server.py 不存在"
fi

if [ -f "log_setup.py" ]; then
    echo "✅ log_setup.py 存在"
else
    echo "❌ log_setup.py 不存在"
fi

if [ -f "GraphRAG/admission_clean.db.gz" ]; then
    echo "✅ 数据库压缩文件存在"
else
    echo "❌ 数据库压缩文件不存在"
fi

echo ""
echo "=== 验证完成 ==="