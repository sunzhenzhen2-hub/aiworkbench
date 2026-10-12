#!/bin/bash
# 安装 nginx 反代：仅工作台；人才画像 / 学习流均不走 nginx
# 80 空闲则听 80；80 已被占用（如本机安全监控）则听 8080
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NGINX_SITE="/etc/nginx/sites-enabled/talent-profile.conf"
SNIPPET="/etc/nginx/snippets/workbench.conf"

mkdir -p /etc/nginx/snippets /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/backup
cp "$ROOT/deploy/nginx/workbench.conf" "$SNIPPET"
cp "$ROOT/deploy/nginx/dsh-embed.conf" /etc/nginx/snippets/dsh-embed.conf
cp "$ROOT/deploy/nginx/websocket-map.conf" /etc/nginx/conf.d/websocket-map.conf

if ss -tln | grep -qE ':80[[:space:]]'; then
  SITE_SRC="$ROOT/deploy/nginx/site-8080.conf"
  LISTEN_PORT=8080
else
  SITE_SRC="$ROOT/deploy/nginx/site-80.conf"
  LISTEN_PORT=80
fi

cp "$SITE_SRC" "$NGINX_SITE"
cp "$ROOT/deploy/nginx/site-80.conf" /etc/nginx/sites-available/workbench-80.conf
cp "$ROOT/deploy/nginx/site-8080.conf" /etc/nginx/sites-available/workbench-8080.conf

find /etc/nginx/sites-enabled -maxdepth 1 -name '*.bak.*' -exec mv {} /etc/nginx/backup/ \;

# 避免与 default 站点抢端口
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl restart nginx
# 防火墙默认只放了 80；走 8080 时必须同样放行，否则同事电脑打不开
if [[ "$LISTEN_PORT" == "8080" ]] && command -v ufw >/dev/null 2>&1; then
  if ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow from 30.251.255.0/24 to any port 8080 proto tcp comment 'aiworkbench nginx' >/dev/null
    ufw allow from 127.0.0.1 to any port 8080 proto tcp comment 'aiworkbench local' >/dev/null
  fi
fi
echo "✅ nginx 已重载（监听 ${LISTEN_PORT}；仅工作台）"
if [[ "$LISTEN_PORT" == "80" ]]; then
  echo "PC 访问:       http://30.251.255.142/aiworkbench/"
  echo "钉钉 H5:       http://30.251.255.142/aiworkbench/m/"
else
  echo "说明: 80 端口已被占用，工作台改走 8080"
  echo "PC 访问:       http://30.251.255.142:8080/aiworkbench/"
  echo "钉钉 H5:       http://30.251.255.142:8080/aiworkbench/m/"
fi
echo "旧路径 /workbench/ 会自动跳到 /aiworkbench/"
