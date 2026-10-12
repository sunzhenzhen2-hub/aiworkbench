import { describe, it, expect } from 'vitest'
import { PROJECT_LEVEL_MAP, TASK_PRIORITY_MAP } from '../constants/projectMeta'

describe('constants', () => {
  it('project level labels', () => {
    expect(PROJECT_LEVEL_MAP.normal).toBe('普通')
    expect(PROJECT_LEVEL_MAP.key).toBe('重点')
    expect(PROJECT_LEVEL_MAP.daily).toBe('日常')
  })

  it('task priority labels', () => {
    expect(TASK_PRIORITY_MAP.p0).toBe('P0')
    expect(TASK_PRIORITY_MAP.p4).toBe('P4')
  })
})
