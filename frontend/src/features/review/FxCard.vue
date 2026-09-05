<script setup lang="ts">
import {computed, reactive, watch} from 'vue';
import {useLocale} from '../../vue/locale.ts';
import {formatDateText} from '../../core/format.ts';
import type {FxChoice,FxSuggestion} from './guided-model.ts';
const props = defineProps<{suggestion: FxSuggestion; choice: FxChoice | null; disabled: boolean; error?: string}>();
const emit = defineEmits<{change:[choice: FxChoice]}>();
const {locale,t} = useLocale();
const manual = computed(() => props.choice?.mode === 'settlement');
const suggested = computed(() => ['exact','prior'].includes(props.suggestion.status));
const fields = reactive({rate:'',date:'',reference:''});
watch(() => props.choice, choice => {
  const next: Partial<FxChoice> = choice?.mode==='settlement' ? choice : {};
  if (fields.rate.trim() !== (next.rate || '')) fields.rate = next.rate || '';
  const date = next.rateDate || props.suggestion.transaction_date || new Date().toISOString().slice(0,10);
  if (fields.date !== date) fields.date = date;
  if (fields.reference.trim() !== (next.sourceReference || '')) fields.reference = next.sourceReference || '';
}, {immediate:true});
const changeMode = (mode:'ecb'|'settlement') => emit('change', mode==='ecb' ? {mode} : {mode,rate:'',rateDate:props.suggestion.transaction_date || new Date().toISOString().slice(0,10),sourceReference:''});
const sync = () => emit('change',{mode:'settlement',rate:fields.rate.trim(),rateDate:fields.date,sourceReference:fields.reference.trim()});
const date = (value?:string) => formatDateText(value,locale.value);
</script>
<template>
  <article class="review-card fx-card" :class="suggestion.status==='existing' ? 'fx-card-existing' : suggested ? manual ? 'fx-card-manual' : 'fx-card-suggested' : 'fx-card-unavailable'">
    <h3>{{t('review.fx')}}</h3>
    <template v-if="suggestion.status==='existing'"><p class="fx-card-status success">{{t('review.fxExisting')}}</p><dl class="review-facts-list"><div><dt>{{t('fields.fxRateDate')}}</dt><dd>{{date(suggestion.rate_date)}}</dd></div><div><dt>{{t('fields.fxRate')}}</dt><dd>1 {{suggestion.currency}} = {{suggestion.eur_per_unit}} EUR</dd></div><div><dt>{{t('fields.fxRateSource')}}</dt><dd>{{suggestion.source_label || suggestion.rate_source || ''}}</dd></div><div v-if="suggestion.amount_eur"><dt>{{t('review.fxAmount')}}</dt><dd>{{suggestion.amount_eur}} EUR</dd></div></dl></template>
    <template v-else>
      <template v-if="suggested"><p class="muted-copy">{{t('review.fxSuggestionLabel')}}</p><label class="fx-choice" :class="{active:!manual}"><input type="radio" name="fx-choice" value="ecb" :checked="!manual" :disabled="disabled" @change="changeMode('ecb')"><span><strong>{{t(suggestion.status==='exact' ? 'review.fxExactRate' : 'review.fxPriorRate',{date:date(suggestion.rate_date)})}}</strong><small>1 {{suggestion.currency}} = {{suggestion.eur_per_unit}} EUR<template v-if="suggestion.amount_eur"> · {{t('review.fxAmount')}} {{suggestion.amount_eur}} EUR</template></small></span></label><label class="fx-choice" :class="{active:manual}"><input type="radio" name="fx-choice" value="settlement" :checked="manual" :disabled="disabled" @change="changeMode('settlement')"><span><strong>{{t('review.fxUseManual')}}</strong></span></label></template>
      <template v-else><p class="fx-card-status warning">{{t('review.fxUnavailable')}}</p><p class="muted-copy">{{suggestion.note && suggestion.note !== 'manual_settlement_required' ? suggestion.note : t('review.fxUnavailableHint')}}</p></template>
      <div v-if="manual || !suggested" class="form-grid compact-grid fx-settlement-grid"><label><span>{{t('review.fxSettlementRate',{currency:suggestion.currency || 'USD'})}}</span><input id="review-fx-settlement-rate" v-model="fields.rate" type="number" min="0" step="0.00000001" inputmode="decimal" :disabled="disabled" @input="sync"></label><label><span>{{t('review.fxSettlementDate')}}</span><input id="review-fx-settlement-date" v-model="fields.date" type="date" :disabled="disabled" @input="sync"></label><label class="full-span"><span>{{t('review.fxSettlementReference')}}</span><input id="review-fx-settlement-reference" v-model="fields.reference" type="text" :disabled="disabled" @input="sync"></label></div>
      <p v-if="suggested" class="muted-copy fx-choice-note">{{t('review.fxChoiceNote')}}</p><p v-if="error" class="review-inline-error" role="alert">{{error}}</p>
    </template>
  </article>
</template>
