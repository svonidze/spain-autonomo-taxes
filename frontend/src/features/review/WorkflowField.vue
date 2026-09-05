<script setup lang="ts">
import {ref, watch} from 'vue';
import {useLocale} from '../../vue/locale.ts';
import {formatMessage} from '../../core/i18n.ts';
import type {Scalar, FieldSpec} from './workflow-model.ts';
const props = defineProps<{field: FieldSpec; modelValue?: Scalar}>();
const emit = defineEmits<{'update:modelValue': [value: Scalar]}>();
const {locale} = useLocale();
const display = (value: Scalar | undefined) => value == null ? '' : String(props.field.kind === 'money' || props.field.kind === 'rate' ? Number(value)/100 : props.field.kind === 'ratio' ? Number(value)*100 : value);
const raw = ref(display(props.modelValue));
const read = () => raw.value === '' ? props.field.kind && props.field.kind !== 'text' ? null : '' : props.field.kind === 'money' || props.field.kind === 'rate' ? Math.round(Number(raw.value)*100) : props.field.kind === 'ratio' ? Number(raw.value)/100 : raw.value;
watch(() => props.modelValue, value => {if (value !== read()) raw.value = display(value);});
</script>
<template><label><span>{{formatMessage(field.label, {}, locale)}}</span><input v-model="raw" :data-p="field.path" :data-kind="field.kind || 'text'" :type="field.type || 'text'" :step="field.type === 'number' ? '0.01' : undefined" @input="emit('update:modelValue', read())"></label></template>
