import type {ChartSpec, ChartScene, ChartRenderer} from './types.ts';
export function buildCartesianScene(spec: ChartSpec): ChartScene;
export function buildHorizontalBarsScene(spec: ChartSpec): ChartScene;
export function buildBulletScene(spec: ChartSpec): ChartScene;
export function mountScene(container: HTMLElement, scene: ChartScene): SVGElement;
export const renderCartesian: ChartRenderer;
export const renderHorizontalBars: ChartRenderer;
export const renderBullet: ChartRenderer;
