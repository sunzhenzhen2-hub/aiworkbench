/** 钉钉 JSAPI 免登：获取 authCode（与小程序同路径，不走 PC 扫码） */

const DEFAULT_CORP_ID = 'dingc4aa4304d998d0d2f5bf40eda33b7ba0'

export function getCorpId(): string {
  return import.meta.env.VITE_DINGTALK_CORP_ID || DEFAULT_CORP_ID
}

function waitDdReady(): Promise<void> {
  return new Promise((resolve) => {
    const dd = window.dd
    if (!dd) {
      resolve()
      return
    }
    if (typeof dd.ready === 'function') {
      dd.ready(() => resolve())
      return
    }
    resolve()
  })
}

function failMessage(err: unknown): string {
  if (typeof err === 'object' && err && 'errorMessage' in err) {
    return String((err as { errorMessage: string }).errorMessage)
  }
  if (typeof err === 'object' && err && 'message' in err) {
    return String((err as { message: string }).message)
  }
  return '获取钉钉授权码失败'
}

type AuthSuccess = { code?: string; authCode?: string }

function pickCode(res: AuthSuccess | undefined): string | null {
  const code = res?.code || res?.authCode
  return code ? String(code) : null
}

/** 请求钉钉免登授权码（须在钉钉客户端内打开） */
export async function requestDingTalkAuthCode(corpId = getCorpId()): Promise<string> {
  await waitDdReady()
  const dd = window.dd as typeof window.dd & {
    runtime?: {
      permission?: {
        requestAuthCode?: (opts: {
          corpId: string
          onSuccess?: (res: AuthSuccess) => void
          onFail?: (err: unknown) => void
        }) => void
      }
    }
  }
  if (!dd) {
    throw new Error('请在钉钉手机里打开本应用（不要用外部浏览器）')
  }

  return new Promise((resolve, reject) => {
    const fail = (err: unknown) => reject(new Error(failMessage(err)))
    const ok = (res: AuthSuccess | undefined) => {
      const code = pickCode(res)
      if (code) resolve(code)
      else fail(res)
    }

    // 1) 企业内 H5 最常用：runtime.permission.requestAuthCode
    if (dd.runtime?.permission?.requestAuthCode) {
      dd.runtime.permission.requestAuthCode({
        corpId,
        onSuccess: ok,
        onFail: fail,
      })
      return
    }

    // 2) 新版 JSAPI
    if (typeof dd.requestAuthCode === 'function') {
      dd.requestAuthCode({
        corpId,
        onSuccess: ok,
        onFail: fail,
      })
      return
    }

    // 3) 兼容旧版 / 小程序风格 getAuthCode
    if (typeof dd.getAuthCode === 'function') {
      dd.getAuthCode({
        corpId,
        success: ok,
        fail,
      })
      return
    }

    reject(new Error('当前钉钉版本不支持免登，请升级钉钉后重试'))
  })
}
