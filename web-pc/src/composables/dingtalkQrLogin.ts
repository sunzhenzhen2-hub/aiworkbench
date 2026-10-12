/**
 * 钉钉 PC 扫码：加载官方 DDLogin SDK + DTFrameLogin 页内嵌二维码
 * 对齐人才画像 vue3-admin/src/composables/dingtalkQrLogin.js
 */

const DEFAULT_SDK = 'https://g.alicdn.com/dingding/h5-dingtalk-login/0.30.1/ddlogin.js'
const QR_WAIT_HINT = '钉钉等了太久没响应，请点「刷新二维码」再扫'

export function friendlyDingTalkScanError(raw: unknown): string {
  const msg = String(
    (raw as { message?: string; response?: { data?: { detail?: string } } })?.response?.data
      ?.detail ||
      (raw as { message?: string })?.message ||
      raw ||
      '',
  )
  if (/timeout of \d+ms exceeded/i.test(msg) || /ECONNABORTED/i.test(msg)) {
    return QR_WAIT_HINT
  }
  return msg || '扫码失败'
}

export function shouldReloadDingTalkQr(raw: unknown): boolean {
  const msg = String((raw as { message?: string })?.message || raw || '')
  return /timeout of \d+ms exceeded/i.test(msg)
}

export type DingTalkQrConfig = {
  client_id: string
  redirect_uri: string
  state: string
  scope?: string
  response_type?: string
  prompt?: string
  corp_id?: string
  sdk_url?: string
}

export type DingTalkQrSuccess = {
  authCode: string
  state?: string
  redirectUrl?: string
}

declare global {
  interface Window {
    DTFrameLogin?: (
      frameParams: { id: string; width: number; height: number },
      loginParams: Record<string, string>,
      onSuccess: (result: { authCode?: string; code?: string; state?: string; redirectUrl?: string }) => void,
      onError: (errorMsg: string) => void,
    ) => void
  }
}

let sdkPromise: Promise<void> | null = null

export function loadDingTalkLoginSdk(sdkUrl = DEFAULT_SDK): Promise<void> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('非浏览器环境'))
  }
  if (typeof window.DTFrameLogin === 'function') {
    return Promise.resolve()
  }
  if (sdkPromise) return sdkPromise

  sdkPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-dt-login-sdk]')
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', () => reject(new Error('钉钉登录 SDK 加载失败')))
      return
    }
    const script = document.createElement('script')
    script.src = sdkUrl
    script.async = true
    script.dataset.dtLoginSdk = '1'
    script.onload = () => resolve()
    script.onerror = () => {
      sdkPromise = null
      reject(new Error('钉钉登录 SDK 加载失败'))
    }
    document.head.appendChild(script)
  })
  return sdkPromise
}

export async function mountDingTalkQrLogin(opts: {
  containerId: string
  config: DingTalkQrConfig
  width?: number
  height?: number
  onSuccess: (r: DingTalkQrSuccess) => void
  onError?: (msg: string) => void
}): Promise<void> {
  const {
    containerId,
    config,
    width = 300,
    height = 300,
    onSuccess,
    onError,
  } = opts

  if (!config?.client_id || !config?.redirect_uri || !config?.state) {
    throw new Error('钉钉扫码配置不完整')
  }

  // VPN / 内网穿透场景下登录页与 redirect_uri 可能不同源，仅校验 URI 可解析
  let redirectOrigin = ''
  try {
    redirectOrigin = new URL(config.redirect_uri).origin
  } catch {
    throw new Error('redirect_uri 非法')
  }
  if (typeof window !== 'undefined' && redirectOrigin !== window.location.origin) {
    console.warn(
      `[dingtalkQrLogin] redirect_uri 与登录页不同源（redirect=${redirectOrigin}, page=${window.location.origin}），VPN 场景允许继续`,
    )
  }

  await loadDingTalkLoginSdk(config.sdk_url || DEFAULT_SDK)
  if (typeof window.DTFrameLogin !== 'function') {
    throw new Error('DTFrameLogin 不可用')
  }

  const el = document.getElementById(containerId)
  if (el) el.innerHTML = ''

  const loginParams: Record<string, string> = {
    redirect_uri: encodeURIComponent(config.redirect_uri),
    client_id: config.client_id,
    scope: config.scope || 'openid',
    response_type: config.response_type || 'code',
    state: config.state,
    prompt: config.prompt || 'consent',
  }
  // 仅当 scope 含 corpid 时才传 corpId
  const scopeStr = String(loginParams.scope)
  if (config.corp_id && /corpid/i.test(scopeStr)) {
    loginParams.corpId = config.corp_id
  }

  window.DTFrameLogin(
    { id: containerId, width, height },
    loginParams,
    (loginResult) => {
      const authCode = loginResult?.authCode || loginResult?.code
      const state = loginResult?.state || config.state
      if (!authCode) {
        onError?.('未拿到 authCode')
        return
      }
      onSuccess?.({ authCode, state, redirectUrl: loginResult?.redirectUrl })
    },
    (errorMsg) => {
      onError?.(String(errorMsg || '钉钉扫码失败'))
    },
  )
}

export function clearDingTalkQrContainer(containerId: string): void {
  const el = document.getElementById(containerId)
  if (el) el.innerHTML = ''
}
