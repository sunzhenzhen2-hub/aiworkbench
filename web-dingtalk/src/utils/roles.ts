/** 系统角色 code → 中文名（给用户看的标签） */
const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  dept_head: '部门负责人',
  project_owner: '项目负责人',
  member: '普通成员',
}

export function roleLabel(code: string): string {
  return ROLE_LABELS[code] || code
}

export function roleLabels(codes: string[] | undefined): string[] {
  if (!codes?.length) return ['普通成员']
  return codes.map(roleLabel)
}

/** 管理首页：能看全员/全部门项目的人 */
export function isManagerHome(permissions: string[] | undefined): boolean {
  const perms = permissions || []
  return perms.includes('report.view.all') || perms.includes('project.view.all')
}
