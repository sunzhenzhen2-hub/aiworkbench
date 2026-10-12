/** 项目类型与规范阶段（与后端 /projects/meta/types 对齐） */
export const PROJECT_TYPE_MAP: Record<string, string> = {
  software: '软件开发项目',
  integration: '系统集成项目',
  fusion: '集众融合项目',
}

/** 项目分级：普通 / 重点 / 日常 */
export const PROJECT_LEVEL_MAP: Record<string, string> = {
  normal: '普通',
  key: '重点',
  daily: '日常',
}

export const PROJECT_TYPE_HINTS: Record<string, string> = {
  fusion: '如智安校园等融合类产品',
  integration: '弱电、工程、硬件、运维、城域网等',
  software: '软件/系统/小程序等开发类',
}

/** 任务级别 P0（最高）～ P4（最低） */
export const TASK_PRIORITY_MAP: Record<string, string> = {
  p0: 'P0',
  p1: 'P1',
  p2: 'P2',
  p3: 'P3',
  p4: 'P4',
}

export const TASK_PRIORITY_HINT = 'P0 最高会提醒责任人；总览「重点任务」含 P0/P1'

/** 软件 / 集众融合默认阶段 */
export const SOFTWARE_PHASES: string[] = [
  '需求分析',
  '立项',
  '设计',
  '开发',
  '测试',
  '上线',
  '运维保障',
  '验收',
  '收尾',
]

/** 系统集成默认阶段 */
export const INTEGRATION_PHASES: string[] = [
  '需求分析',
  '立项',
  '方案设计',
  '采购到货',
  '现场实施',
  '联调测试',
  '验收交付',
  '运维保障',
  '收尾',
]

export const PHASES_BY_TYPE: Record<string, string[]> = {
  software: SOFTWARE_PHASES,
  fusion: SOFTWARE_PHASES,
  integration: INTEGRATION_PHASES,
}

/** 规范项目阶段（默认软件模板，兼容旧引用） */
export const STANDARD_PHASES: string[] = [...SOFTWARE_PHASES]

/** 工作台汇总排序用：软件 + 集成专有名 */
export const ALL_DISPLAY_PHASES: string[] = (() => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const name of [...SOFTWARE_PHASES, ...INTEGRATION_PHASES, '实施/开发']) {
    if (!seen.has(name)) {
      seen.add(name)
      out.push(name)
    }
  }
  return out
})()

export function phasesForType(projectType: string | null | undefined): string[] {
  const key = (projectType || 'software').trim()
  return [...(PHASES_BY_TYPE[key] || SOFTWARE_PHASES)]
}

/** 从「项目名-任务描述」拆分（前端粘贴周报标题时用） */
export function splitProjectAndTask(raw: string): { projectName: string; taskTitle: string | null } {
  const t = (raw || '').trim()
  if (!t) return { projectName: '', taskTitle: null }
  for (const sep of [' - ', '——', '–', '-'] as const) {
    if (t.includes(sep)) {
      const i = t.indexOf(sep)
      const left = t.slice(0, i).trim()
      const right = t.slice(i + sep.length).trim()
      if (left.length >= 2 && right) return { projectName: left, taskTitle: right }
      break
    }
  }
  return { projectName: t, taskTitle: null }
}
