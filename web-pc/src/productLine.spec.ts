import { describe, expect, it } from 'vitest'
import { homePathAfterLogin, workbenchHref } from './productLine'

describe('workbenchHref', () => {
  it('keeps the page path after the app base', () => {
    expect(workbenchHref('/dsh').endsWith('/dsh')).toBe(true)
    expect(workbenchHref('personal').endsWith('/personal')).toBe(true)
  })
})

describe('homePathAfterLogin', () => {
  it('falls back to personal when the user cannot open the assistant', () => {
    expect(homePathAfterLogin(false)).toBe('/personal')
  })
})
