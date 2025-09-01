// frontend/app/stores/recording.ts
import { defineStore } from 'pinia'
import api from '@/lib/apiClient'
import { ref } from 'vue'
import { useProjectStore } from '@/stores/project'

export const useRecordingStore = defineStore('recording', () => {
  const project = useProjectStore()
  const base = ref(project.backendUrl)

  const active = ref(false)
  const count = ref(0)
  const elapsed_ms = ref(0)

  let timer: number | null = null

  async function tick() {
    try {
      const s = await api.record.state()
      active.value = !!s.active
      count.value = Number(s.count || 0)
      elapsed_ms.value = Number(s.elapsed_ms || 0)
    } catch {
      // ignore fetch error
    }
  }

  function start(pollMs = 1000) {
    if (timer) return
    timer = window.setInterval(tick, pollMs)
    tick()
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function setBackendUrl(url: string) {
    base.value = url
  }

  return {
    active,
    count,
    elapsed_ms,
    start,
    stop,
    setBackendUrl,
    refresh: tick,
  }
})
