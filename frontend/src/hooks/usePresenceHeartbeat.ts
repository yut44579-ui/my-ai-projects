import { useEffect, useRef, useState } from 'react'
import { apiPost } from '../api/client'
import type { PresenceStatus } from '../api/types'

/** 心跳间隔（秒）。★ 必须与后端 HEARTBEAT_INTERVAL_SECONDS 一致。 */
export const HEARTBEAT_INTERVAL_SECONDS = 30

export type HeartbeatResponse = {
  status: PresenceStatus
  label: string
  reason: string
  is_override: boolean
  seconds_since_active: number | null
  server_time: string
}

/**
 * 实时状态心跳（TASK-040）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【它做什么】
 * ═══════════════════════════════════════════════════════════════
 *   每 30 秒向后端发一次心跳，并告诉后端"这段时间用户有没有真的在操作"：
 *     · 页面打开着         → 一直发心跳（后端据此判断"页面还开着"）
 *     · 有鼠标/键盘/滚动/切回标签页 → 这次心跳带 active=true
 *   后端据此推算 在线 / 忙碌 / 离线，前端拿回来直接显示。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【为什么要区分"页面开着"与"人在操作"】
 * ═══════════════════════════════════════════════════════════════
 *   ★ 只发心跳的话，人离开工位但页面没关，系统会一直认为他"在线" ——
 *     同事找他找不到人，这个状态就是错的。
 *   ★ 只看操作的话，切到别的窗口就会误判成离线。
 *   两者结合才能得到有意义的三态（这正是常见 IM 的做法）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【几个刻意的处理】
 * ═══════════════════════════════════════════════════════════════
 *   · **页面隐藏时降低心跳频率**（浏览器会节流后台标签页的定时器，
 *     所以不指望精确；能省电省请求就行）
 *   · **关闭页面时发一次离线信号**（`navigator.sendBeacon`）——
 *     否则要等 90 秒心跳超时，同事会看到"还在线"的假象
 *     ★ 用 sendBeacon 而不是 fetch：页面卸载时 fetch 会被浏览器取消，
 *       sendBeacon 是专门为这个场景设计的，一定会发出去。
 *   · **请求失败不打扰用户**：心跳失败静默重试，不弹错误提示
 *     （网络抖一下就弹红条，比状态不准更烦人）
 */
export function usePresenceHeartbeat(options?: { onStatus?: (r: HeartbeatResponse) => void }) {
  const [live, setLive] = useState<HeartbeatResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  // ★ 用 ref 存"是否有未上报的操作"：监听器与定时器是两套生命周期，
  //   用 state 会让监听器闭包拿到旧值（经典陷阱）。
  const pendingActive = useRef(false)
  const onStatusRef = useRef(options?.onStatus)
  onStatusRef.current = options?.onStatus

  useEffect(() => {
    // 没登录就不发心跳（登录页不该有心跳）
    if (!localStorage.getItem('auth_token')) return

    let cancelled = false

    const markActive = () => {
      pendingActive.current = true
    }

    // ★ 监听范围："用户真的动了"的几种信号。
    //   刻意**不监听 mousemove 的每一次**（那样每秒几十次回调），
    //   只事件本身设置一个标记位，真正的判定留给心跳定时器。
    const events: (keyof WindowEventMap)[] = [
      'mousedown',
      'keydown',
      'wheel',
      'touchstart',
      'scroll',
    ]
    events.forEach((e) => window.addEventListener(e, markActive, { passive: true }))
    // 切回本标签页也算"人在"——很多人是切出去看资料再切回来
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        markActive()
        void beat()
      }
    }
    document.addEventListener('visibilitychange', onVisible)

    async function beat() {
      const active = pendingActive.current
      pendingActive.current = false
      try {
        const res = await apiPost<HeartbeatResponse>('/api/profile/heartbeat', { active })
        if (cancelled) return
        setLive(res)
        setError(null)
        onStatusRef.current?.(res)
      } catch (err) {
        // ★ 心跳失败不弹提示：网络抖一下就报错比状态不准更打扰人
        if (!cancelled) setError((err as { message?: string }).message ?? '心跳失败')
      }
    }

    void beat()
    const timer = window.setInterval(() => {
      // ★ 后台标签页降频：浏览器对后台定时器有节流，这里再主动放宽到 2 倍，
      //   既减少无谓请求，也不会因为节流而彻底停掉
      if (document.visibilityState === 'hidden') return
      void beat()
    }, HEARTBEAT_INTERVAL_SECONDS * 1000)

    // ★ 关闭/刷新页面时主动上报"离线"，让同事立刻看到正确状态，
    //   而不是等 90 秒心跳超时。
    //
    // ★ 用 `fetch(..., {keepalive:true})` 而**不是** `navigator.sendBeacon`：
    //   sendBeacon 无法自定义请求头，只能把令牌塞进 URL —— 那会让令牌
    //   出现在服务器访问日志里（等于把凭据写进日志）。
    //   fetch + keepalive 同样能在页面卸载时把请求发出去，且能带 Authorization 头。
    const onUnload = () => {
      try {
        const token = localStorage.getItem('auth_token')
        if (!token) return
        void fetch('/api/profile/heartbeat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ active: false, leaving: true }),
          keepalive: true,
        })
      } catch {
        // 卸载阶段任何异常都不能影响页面关闭
      }
    }
    window.addEventListener('pagehide', onUnload)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      events.forEach((e) => window.removeEventListener(e, markActive))
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('pagehide', onUnload)
    }
  }, [])

  return { live, error }
}
