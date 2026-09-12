#!/bin/bash
# ====================================================================
# football 项目 Streamlit 后端一键部署脚本（Ubuntu/Debian）
# ====================================================================
# 用法：
#   1. 把这个文件传到服务器：scp deploy_streamlit.sh root@服务器IP:/root/
#   2. ssh root@服务器IP
#   3. chmod +x deploy_streamlit.sh
#   4. sudo ./deploy_streamlit.sh
# ====================================================================

set -e    # 任一步失败立即终止

REPO_URL="${1:-https://github.com/yourname/football.git}"
APP_DIR="/root/football"
APP_PORT=8501
NGINX_PORT=80

echo "╔════════════════════════════════════════════╗"
echo "║  Football Streamlit 一键部署脚本              ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# ──────────────── 步骤 1/10：检查系统 ────────────────
echo "▶ [1/10] 检查系统环境..."
if ! command -v python3.11 &> /dev/null; then
    echo "▶ 安装 Python 3.11..."
    sudo apt update
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.11 python3.11-venv python3-pip
fi
[ ! -d "python3.11" ] && sudo apt install -y python3.11-venv
echo "  ✓ Python 就绪"

# ──────────────── 步骤 1b/10：装中文字体（matplotlib 用） ────────────────
echo "▶ [1b/10] 安装中文字体..."
echo "    （程序里 _resolve_cjk_font() 会在 Linux 上找 Noto Sans CJK SC / WenQuanYi Zen Hei）"
if command -v apt &> /dev/null; then
    sudo apt install -y fonts-noto-cjk fonts-wqy-microhei fonts-wqy-zenhei
elif command -v yum &> /dev/null; then
    sudo yum install -y google-noto-sans-cjk-fonts wqy-microhei-fonts wqy-zenhei-fonts
fi
fc-cache -fv

# 验证字体
if fc-list :lang=zh | grep -q "Noto Sans CJK"; then
    echo "  ✓ Noto Sans CJK SC 已安装"
else
    echo "  ⚠ 未检测到 Noto Sans CJK，可能后续图表中文显示异常"
fi

# ──────────────── 步骤 2/10：装 nginx + git ────────────────
echo "▶ [2/10] 安装 nginx + git..."
sudo apt install -y nginx git curl
echo "  ✓ nginx / git 就绪"

# ──────────────── 步骤 3/10：拉代码 ────────────────
echo "▶ [3/10] 拉取项目代码..."
if [ ! -d "$APP_DIR" ]; then
    cd /root
    git clone "$REPO_URL" football
    echo "  ✓ 已克隆"
else
    cd "$APP_DIR"
    git pull
    echo "  ✓ 已更新到最新"
fi
cd "$APP_DIR"

# ──────────────── 步骤 4/10：建虚拟环境 ────────────────
echo "▶ [4/10] 建立 Python 虚拟环境..."
if [ ! -d "venv" ]; then
    python3.11 -m venv venv
fi
source venv/bin/activate
echo "  ✓ 虚拟环境已激活"

# ──────────────── 步骤 5/10：装依赖 ────────────────
echo "▶ [5/10] 安装 Python 依赖..."
pip install --upgrade pip
if [ -f "requirements-full.txt" ]; then
    pip install -r requirements-full.txt
elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi
echo "  ✓ 依赖已装"

# 清掉 matplotlib 字体缓存，强制它重新扫描系统字体
rm -rf ~/.cache/matplotlib
echo "  ✓ 已清空 matplotlib 字体缓存（让 matplotlib 重新发现 Noto Sans CJK）"

# ──────────────── 步骤 6/10：建目录结构 ────────────────
echo "▶ [6/10] 创建 logs / data 目录..."
mkdir -p logs data
echo "  ✓ 目录就绪"

# ──────────────── 步骤 7/10：建 systemd 服务 ────────────────
echo "▶ [7/10] 配置 systemd 服务..."
SERVICE_FILE=/etc/systemd/system/streamlit.service
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Football Analytics Streamlit App
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin:/usr/local/bin:/usr/bin"
ExecStart=$APP_DIR/venv/bin/streamlit run app/ui/app.py \\
          --server.port $APP_PORT \\
          --server.address 0.0.0.0 \\
          --server.headless true
Restart=always
RestartSec=5
StandardOutput=append:$APP_DIR/logs/streamlit.log
StandardError=append:$APP_DIR/logs/streamlit.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable streamlit
sudo systemctl restart streamlit
echo "  ✓ systemd 服务已配置并启动"

# ──────────────── 步骤 8/10：配置 nginx ────────────────
echo "▶ [8/10] 配置 nginx 反向代理..."
NGINX_FILE=/etc/nginx/sites-available/streamlit
sudo tee "$NGINX_FILE" > /dev/null << EOF
server {
    listen $NGINX_PORT;
    server_name _;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }
}
EOF

sudo ln -sf "$NGINX_FILE" /etc/nginx/sites-enabled/streamlit
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx
echo "  ✓ nginx 已配置"

# ──────────────── 步骤 9/10：开放防火墙 ────────────────
echo "▶ [9/10] 配置防火墙..."
if command -v ufw &> /dev/null; then
    sudo ufw allow $NGINX_PORT/tcp
    sudo ufw allow 443/tcp
    sudo ufw --force enable
    echo "  ✓ ufw 已配置"
else
    echo "  ⚠ 未找到 ufw，请手动在云控制台安全组开放 80 端口"
fi

# ──────────────── 步骤 10/10：完成 ────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✅ 部署完成！                                  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "  访问地址：http://$(curl -s ifconfig.me || echo '你的服务器IP')"
echo ""
echo "  常用命令："
echo "    systemctl status  streamlit    ← 看 streamlit 状态"
echo "    systemctl restart streamlit    ← 重启 streamlit"
echo "    tail -f logs/streamlit.log     ← 实时看 streamlit 日志"
echo "    systemctl status  nginx        ← 看 nginx 状态"
echo ""
