/** 项目经理、项目支撑的人选规则，新建项目和成员角色共用。 */

export interface RolePerson {
  id: number
  name: string
  job_title?: string
}

export const PROJECT_MANAGER_NAMES = ['许金斌', '周永祺']
export const SUPPORT_EXCLUDED_NAME = '孙振振'

export function isProjectManagerName(name: string | null | undefined): boolean {
  return PROJECT_MANAGER_NAMES.includes((name || '').trim())
}

export function isProjectSupportPerson(user: RolePerson): boolean {
  const name = (user.name || '').trim()
  if (!name || name === SUPPORT_EXCLUDED_NAME) return false
  if (PROJECT_MANAGER_NAMES.includes(name)) return false
  return true
}

export function pickManagerOptions<T extends RolePerson>(users: T[]): T[] {
  const picked = new Map<string, T>()
  for (const user of users) {
    const name = (user.name || '').trim()
    if (!PROJECT_MANAGER_NAMES.includes(name) || picked.has(name)) continue
    picked.set(name, user)
  }
  return [...picked.values()]
}

export function pickSupportOptions<T extends RolePerson>(users: T[]): T[] {
  return users.filter((user) => isProjectSupportPerson(user))
}

/** 下拉里补上当前已选人（历史脏数据也能显示姓名，避免只显示数字 ID） */
export function withSelectedOptions<T extends RolePerson>(
  options: T[],
  selectedIds: Array<number | null | undefined>,
  allUsers: T[],
): T[] {
  const byId = new Map(options.map((u) => [u.id, u]))
  for (const id of selectedIds) {
    if (id == null || byId.has(id)) continue
    const found = allUsers.find((u) => u.id === id)
    if (found) byId.set(id, found)
  }
  return [...byId.values()]
}

export function defaultRolesForCreator(user: { id: number; name: string } | null | undefined): {
  lead_user_id: number | null
  support_user_ids: number[]
} {
  if (!user) return { lead_user_id: null, support_user_ids: [] }
  const name = (user.name || '').trim()
  if (isProjectManagerName(name)) {
    return { lead_user_id: user.id, support_user_ids: [] }
  }
  if (name && name !== SUPPORT_EXCLUDED_NAME) {
    return { lead_user_id: null, support_user_ids: [user.id] }
  }
  return { lead_user_id: null, support_user_ids: [] }
}
