import { describe, expect, it } from 'vitest'
import { friendlyDingTalkScanError, shouldReloadDingTalkQr } from './dingtalkQrLogin'

describe('friendlyDingTalkScanError', () => {
  it('turns axios 15s timeout into a Chinese hint', () => {
    expect(friendlyDingTalkScanError('timeout of 15000ms exceeded')).toContain('刷新二维码')
    expect(shouldReloadDingTalkQr('timeout of 15000ms exceeded')).toBe(true)
  })

  it('keeps server detail text', () => {
    expect(
      friendlyDingTalkScanError({ response: { data: { detail: '连不上钉钉，请稍后刷新二维码重试' } } }),
    ).toContain('连不上钉钉')
  })
})
