/** 与后端 app/core/password_policy.py 规则保持一致 */
export const PASSWORD_RULES = [
  '至少 8 位，不超过 128 位',
  '至少 1 个大写字母（A-Z）',
  '至少 1 个小写字母（a-z）',
  '至少 1 个数字（0-9）',
  '须同时包含数字与大小写字母混合',
  '不能是常见弱密码',
]

const WEAK = new Set(['password', '123456', '12345678', 'admin123', 'manager123', 'owner123', 'member123'])

export function validateStrongPassword(password: string): string | null {
  if (password.length < 8) return '密码至少 8 位'
  if (password.length > 128) return '密码不能超过 128 位'
  if (WEAK.has(password.toLowerCase())) return '密码过于简单，请更换'
  if (!/[A-Z]/.test(password)) return '密码须包含大写字母'
  if (!/[a-z]/.test(password)) return '密码须包含小写字母'
  if (!/\d/.test(password)) return '密码须包含数字'
  return null
}
