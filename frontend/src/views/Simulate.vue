<template>
  <div>
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景一：实时流</span></template>
          <p class="desc">各设备以当前时间上报，部分高风险因子超标。验证：接收日志确认 → 质量结论 → 15 分钟内告警。</p>
          <el-input-number v-model="rt.count" :min="1" :max="500" size="small" /> 条
          <el-button type="primary" size="small" :loading="loading" @click="run('realtime')">注入实时流</el-button>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景二：断网恢复补传流</span></template>
          <p class="desc">网关重连后把数小时缓存读数带新会话集中上传。验证：恢复编排器按分区游标回补，分区最终一致。</p>
          <el-input-number v-model="bf.hours" :min="0.1" :max="6" :step="0.5" size="small" /> 小时
          <el-button type="primary" size="small" :loading="loading" @click="run('backfill')">注入补传流</el-button>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景三：冲突重传</span></template>
          <p class="desc">同会话同序列：先发正常值，再同指纹重传，最后发不同载荷。验证：重传只确认一次；冲突并列存证不覆盖。</p>
          <el-button type="primary" size="small" :loading="loading" @click="run('conflict')">注入冲突重传</el-button>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景四：数据库写入持续变慢</span></template>
          <p class="desc">开启后每次写入注入延迟，跨过高风险阈值触发降级：暂停低风险衍生计算，高风险分区保留写入额度。</p>
          <el-switch v-model="slow.enabled" active-text="开启慢写入" @change="toggleSlow" />
          <el-input-number v-model="slow.delay_ms" :min="100" :max="5000" :step="100" size="small" style="margin-left: 12px" /> ms
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景五：规则维护 → 待判 → 补判</span></template>
          <p class="desc">开启 COD 规则维护并注入超标读数：维护期读数保留为待判、不告警；自动关闭维护后补判，重新产生应得告警（标记为补判重演）。</p>
          <el-select v-model="mt.rule_code" size="small" style="width: 170px">
            <el-option label="R-COD-RANGE" value="R-COD-RANGE" />
            <el-option label="R-NH3-RANGE" value="R-NH3-RANGE" />
            <el-option label="R-SO2-RANGE" value="R-SO2-RANGE" />
          </el-select>
          <el-select v-model="mt.strategy" size="small" style="width: 150px; margin-left: 8px">
            <el-option label="封存快照" value="snapshot" />
            <el-option label="事件日志" value="event_log" />
          </el-select>
          <el-button type="primary" size="small" style="margin-left: 8px" :loading="loading" @click="runMaintenance">注入维护周期</el-button>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>场景六：回放只读样本</span></template>
          <p class="desc">对只读虚构报送批次执行回放沙箱，计算质量结论与告警影响；若发现已报送读数缺陷则生成待补正事项。</p>
          <el-button type="primary" size="small" :loading="loading" @click="runReplay">回放样本批次</el-button>
          <el-button size="small" @click="$router.push('/replay')">前往回放沙箱</el-button>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <template #header><span>执行结果</span></template>
      <pre class="result">{{ result || '（尚未执行）' }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const rt = reactive({ count: 30 })
const bf = reactive({ hours: 2 })
const slow = reactive({ enabled: false, delay_ms: 1200 })
const mt = reactive({ rule_code: 'R-COD-RANGE', strategy: 'snapshot' })
const loading = ref(false)
const result = ref('')

async function run(kind) {
  loading.value = true
  try {
    const body = kind === 'realtime' ? rt : kind === 'backfill' ? bf : {}
    const { data } = await api.post(`/simulate/${kind}`, body)
    result.value = JSON.stringify(data, null, 2)
    ElMessage.success('场景已注入，请到运行台/接收日志/恢复编排查看')
  } finally {
    loading.value = false
  }
}

async function runMaintenance() {
  loading.value = true
  try {
    const { data } = await api.post('/simulate/maintenance-cycle', { ...mt })
    result.value = JSON.stringify(data, null, 2)
    ElMessage.success('维护周期已注入，请到待判队列/告警查看补判结果')
  } finally {
    loading.value = false
  }
}

async function runReplay() {
  loading.value = true
  try {
    const { data } = await api.post('/replay/run', { batch_code: 'SAMPLE-2026-11-A' })
    result.value = JSON.stringify(data, null, 2)
    ElMessage.success('样本回放完成，请到回放沙箱/影响通知册查看')
  } finally {
    loading.value = false
  }
}

async function toggleSlow(val) {
  const { data } = await api.post('/simulate/slow-writes', { enabled: val, delay_ms: slow.delay_ms })
  result.value = JSON.stringify(data, null, 2)
  ElMessage.warning(val ? '慢写入已开启，观察运行台降级状态' : '慢写入已关闭，观察调度器恢复')
}

onMounted(async () => {
  const { data } = await api.get('/simulate/flags')
  slow.enabled = data.slow_write_enabled
  slow.delay_ms = data.slow_write_delay_ms || 1200
})
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.desc { color: #606266; font-size: 13px; min-height: 40px; }
.result { background: #0f2a43; color: #b8c7d6; padding: 12px; border-radius: 4px; max-height: 320px; overflow: auto; }
</style>
