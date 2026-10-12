/** 142 助手新产品为 true；64 老工作台不设此变量，保持原入口。 */
export function isAgentNativeProduct(): boolean {
  const v = import.meta.env.VITE_AGENT_NATIVE
  return v === '1' || v === 'true'
}

export const AGENT_NATIVE_HINT = '请到「我的助手」里说，确认后再写入账本。'

export function applyProductDocumentTitle(): void {
  if (typeof document === 'undefined') return
  document.title = isAgentNativeProduct() ? '我的助手' : '项目支撑工作台'
}
