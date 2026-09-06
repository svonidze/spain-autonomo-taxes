<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { formatMessage, type MessageId } from '../../core/i18n.ts';
import { eur } from '../../core/format.ts';
export interface DecisionFieldSpec {
  path: string;
  label: MessageId;
  type?: 'text' | 'number' | 'textarea' | 'select' | 'checkbox';
  valueType?: 'string' | 'nullable-string' | 'integer' | 'number' | 'boolean' | 'nullable-boolean';
  options?: { value: string; label: string }[];
  min?: string;
  max?: string;
  step?: string;
  minor?: boolean;
  full?: boolean;
  descriptionId?: string;
}
const props = defineProps<{
  field: DecisionFieldSpec;
  value?: unknown;
  disabled?: boolean;
  guided?: boolean;
  error?: string;
}>();
const emit = defineEmits<{ change: [value: unknown] }>();
const { locale, t } = useLocale();
const display = (value: unknown) => (value == null ? '' : String(value));
const raw = ref(display(props.value));
function read() {
  const type = props.field.valueType || 'string';
  if (raw.value === '') return type.startsWith('nullable-') ? null : '';
  if (type === 'integer') return Number.parseInt(raw.value, 10);
  if (type === 'number') return Number(raw.value);
  if (type === 'boolean' || type === 'nullable-boolean') return raw.value === 'true';
  return raw.value;
}
watch(
  () => props.value,
  (value) => {
    if (read() !== value) raw.value = display(value);
  },
);
const preview = computed(() => {
  const value = Number.parseInt(raw.value, 10);
  return String(raw.value).trim() && Number.isFinite(value)
    ? t('review.irpfPreview', { amount: eur(value / 100, locale.value) })
    : '';
});
</script>
<template>
  <label
    :class="{
      'review-guided-field': guided,
      'full-span': field.full,
      'checkbox-label': field.type === 'checkbox',
    }"
  >
    <input
      v-if="field.type === 'checkbox'"
      type="checkbox"
      :checked="Boolean(value)"
      :disabled="disabled"
      :data-decision-path="field.path"
      data-value-type="boolean"
      @change="emit('change', ($event.target as HTMLInputElement).checked)"
    />
    <span
      >{{ formatMessage(field.label, {}, locale) }}
      <output v-if="field.minor" class="review-irpf-preview" data-eur-preview>{{
        preview
      }}</output></span
    >
    <textarea
      v-if="field.type === 'textarea'"
      v-model="raw"
      rows="2"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      @input="emit('change', read())"
    ></textarea>
    <select
      v-else-if="field.type === 'select'"
      v-model="raw"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      :aria-describedby="field.descriptionId"
      @change="emit('change', read())"
    >
      <option v-for="option in field.options || []" :key="option.value" :value="option.value">
        {{ option.label }}
      </option>
    </select>
    <input
      v-else-if="field.type !== 'checkbox'"
      v-model="raw"
      :type="field.type || 'text'"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      :min="field.min"
      :max="field.max"
      :step="field.step"
      :inputmode="
        field.valueType === 'integer' ? 'numeric' : field.type === 'number' ? 'decimal' : undefined
      "
      @input="emit('change', read())"
    />
    <slot />
    <p v-if="error" class="review-inline-error" role="alert">{{ error }}</p>
  </label>
</template>
