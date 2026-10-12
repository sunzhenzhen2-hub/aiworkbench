#!/bin/bash
# 在 142 本机检查能否访问钉钉开放平台（扫码换票走的就是这些地址）。
# 不带 AppSecret。http 不是 000 就算网络通（400/404 也算通）。
set -u

ok=0
fail=0

check_dns() {
  local host="$1"
  echo "== DNS $host =="
  if timeout 5 getent hosts "$host"; then
    echo "结果：通"
    ok=$((ok + 1))
  else
    echo "结果：不通（解析超时或失败）"
    fail=$((fail + 1))
  fi
  echo
}

check_https() {
  local url="$1"
  echo "== HTTPS $url =="
  local line
  line=$(
    curl -sS -o /dev/null \
      -w "http=%{http_code} connect=%{time_connect}s total=%{time_total}s err=%{errormsg}\n" \
      --connect-timeout 5 --max-time 8 "$url" 2>&1 || true
  )
  echo "$line"
  if echo "$line" | grep -q 'http=000'; then
    echo "结果：不通"
    fail=$((fail + 1))
  elif echo "$line" | grep -qE 'http=[1-5][0-9][0-9]'; then
    echo "结果：通（有 HTTP 状态码即可，不必是 200）"
    ok=$((ok + 1))
  else
    echo "结果：不通"
    fail=$((fail + 1))
  fi
  echo
}

echo "在 $(hostname -I 2>/dev/null | awk '{print $1}') 上检测，请在工作台服务器执行，不要在自己电脑上当结果。"
echo
check_dns api.dingtalk.com
check_dns oapi.dingtalk.com
check_https "https://api.dingtalk.com/v1.0/oauth2/accessToken"
check_https "https://oapi.dingtalk.com/gettoken"

echo "合计：通 ${ok}，不通 ${fail}"
if [ "$fail" -gt 0 ]; then
  echo "扫码登录会 502。请网管放行本机解析并访问上述域名的 HTTPS（443）。"
  exit 1
fi
echo "出口正常，扫码换票网络这一步可以通过。"
exit 0
