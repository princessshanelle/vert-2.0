<script setup>
// Everything plant-cam's latest analysis says, in the order a grower
// acts on it: overall health, what was found (never a diagnosis:
// "suspected" or "detected", with how sure and for how long), disease
// pressure from the climate, harvest, and which model produced it.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import HealthDial from './HealthDial.vue'
import RiskMeter from './RiskMeter.vue'
import RipenessStrip from './RipenessStrip.vue'
import { findingsOf, ripenessOf } from '../lib/agronomy'

const props = defineProps({ reading: { type: Object, required: true } })
const { t } = useI18n()

const findings = computed(() => findingsOf(props.reading))
const ripeness = computed(() => ripenessOf(props.reading))
const risks = computed(() => ['botrytis', 'powdery_mildew']
  .map((id) => ({ id, v: props.reading[`risk_${id}`], hours: props.reading[`risk_${id}_hours`] }))
  .filter((r) => typeof r.v === 'number'))
const models = computed(() => String(props.reading.model_version || '')
  .split(',').filter(Boolean).map((s) => s.split(':').pop()))
const withheld = computed(() => props.reading.confidence !== undefined && props.reading.confidence <= 0.3)

function since(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}
</script>

<template>
  <section class="plant panel" aria-labelledby="plant-head">
    <div class="top">
      <HealthDial :score="reading.health_score" :label="reading.label" :size="148" />
      <div class="summary">
        <h2 id="plant-head">{{ t('device_detail.plant_health') }}</h2>
        <p class="note">{{ reading.note }}</p>
        <dl class="facts">
          <div><dt>{{ t('device_detail.confidence') }}</dt><dd class="tabular">{{ Math.round((reading.confidence ?? 0) * 100) }}%</dd></div>
          <div v-if="reading.canopy_coverage_pct !== undefined"><dt>{{ t('analytics.canopy') }}</dt><dd class="tabular">{{ reading.canopy_coverage_pct.toFixed(0) }}%</dd></div>
          <div v-if="reading.canopy_growth_pct_24h !== undefined"><dt>{{ t('plant.growth_24h') }}</dt><dd class="tabular">{{ reading.canopy_growth_pct_24h >= 0 ? '+' : '' }}{{ reading.canopy_growth_pct_24h.toFixed(1) }}</dd></div>
          <div v-if="reading.lux !== undefined"><dt>{{ t('analytics.light') }}</dt><dd class="tabular">{{ Math.round(reading.lux).toLocaleString() }} lx</dd></div>
        </dl>
        <p v-if="withheld" class="withheld">{{ t('plant.withheld') }}</p>
      </div>
    </div>

    <div class="grid">
      <div class="block">
        <h3>{{ t('plant.findings') }}</h3>
        <ul v-if="findings.length" class="findings">
          <li v-for="f in findings" :key="f.id" :class="{ inactive: !f.active }">
            <div class="f-head">
              <span class="status-chip" :class="f.active ? (f.status === 'detected' ? 'serious' : 'warning') : 'neutral'">
                {{ f.active ? t(`finding.${f.status}`) : t('finding.watching') }}
              </span>
              <strong>{{ t(`conditions.${f.id}`, f.id) }}</strong>
            </div>
            <p class="f-meta muted">
              {{ t('plant.finding_meta', { conf: Math.round((f.confidence ?? 0) * 100), n: f.persistence ?? 0 }) }}
              <template v-if="f.first_seen"> {{ t('plant.since', { when: since(f.first_seen) }) }}</template>
            </p>
          </li>
        </ul>
        <p v-else class="muted">{{ t('overview.no_findings') }}</p>
        <p class="caveat muted">{{ t('plant.caveat') }}</p>
      </div>

      <div v-if="risks.length" class="block">
        <h3>{{ t('overview.disease_pressure') }}</h3>
        <div class="risks"><RiskMeter v-for="r in risks" :key="r.id" :id="r.id" :value="r.v" :hours="r.hours" /></div>
        <p v-if="reading.env_available === 1" class="muted env">
          {{ t('plant.env_line', { temp: reading.env_temp_c?.toFixed(1), rh: Math.round(reading.env_rh_pct ?? 0), dew: reading.env_dew_point_c?.toFixed(1) }) }}
        </p>
      </div>

      <div v-if="ripeness" class="block">
        <h3>{{ t('overview.harvest') }}</h3>
        <RipenessStrip :ripeness="ripeness" />
      </div>
    </div>

    <footer v-if="models.length" class="models muted">
      {{ t('plant.models', { list: models.join(', ') }) }}
    </footer>
  </section>
</template>

<style scoped>
.plant { display: flex; flex-direction: column; gap: 1.4rem; }
.top { display: flex; gap: 1.5rem; align-items: center; flex-wrap: wrap; }
.summary { flex: 1; min-width: 240px; display: flex; flex-direction: column; gap: 0.6rem; }
.note { font-size: 1.05rem; color: var(--ink); max-width: 62ch; }
.facts { display: flex; flex-wrap: wrap; gap: 0.4rem 1.6rem; margin: 0; }
.facts div { display: flex; flex-direction: column; }
.facts dt { font-size: 0.74rem; color: var(--ink-3); font-weight: 600; }
.facts dd { margin: 0; font-weight: 650; }
.withheld { font-size: 0.85rem; color: var(--warning); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1.5rem; border-top: 1px solid var(--line); padding-top: 1.2rem; }
.block h3 { font-size: 0.85rem; color: var(--ink-2); font-family: var(--font-body); font-weight: 650; margin-bottom: 0.65rem; }
.findings { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.75rem; }
.f-head { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.f-meta { font-size: 0.78rem; margin-top: 0.2rem; }
.inactive strong { color: var(--ink-2); font-weight: 600; }
.caveat { font-size: 0.72rem; margin-top: 0.8rem; max-width: 46ch; }
.risks { display: flex; flex-direction: column; gap: 0.7rem; }
.env { font-size: 0.76rem; margin-top: 0.7rem; }
.models { font-size: 0.74rem; border-top: 1px solid var(--line); padding-top: 0.8rem; }
</style>
