/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DINGTALK_CORP_ID?: string
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

interface Window {
  dd?: {
    ready: (fn: () => void) => void
    error?: (fn: (err: unknown) => void) => void
    getAuthCode?: (opts: {
      corpId?: string
      success?: (res: { code?: string; authCode?: string }) => void
      fail?: (err: unknown) => void
    }) => void
    requestAuthCode?: (opts: {
      corpId: string
      onSuccess?: (res: { code?: string; authCode?: string }) => void
      onFail?: (err: unknown) => void
    }) => void
    runtime?: {
      permission?: {
        requestAuthCode?: (opts: {
          corpId: string
          onSuccess?: (res: { code?: string; authCode?: string }) => void
          onFail?: (err: unknown) => void
        }) => void
      }
    }
  }
}
