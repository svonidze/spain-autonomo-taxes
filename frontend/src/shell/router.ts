import {createRouter,createWebHistory} from 'vue-router';
import {views} from './routes.ts';
const placeholder={render:()=>null};
export const router=createRouter({history:createWebHistory(),stringifyQuery(query){const params=new URLSearchParams();for(const [key,value] of Object.entries(query||{})){for(const item of Array.isArray(value)?value:[value])if(item!==undefined)params.append(key,item===null?'':String(item));}return params.toString();},routes:[
  {path:'/',component:placeholder},
  ...views.map(view=>({path:`/${view}`,component:placeholder})),
  ...['contacts','expenses','review'].map(view=>({path:`/${view}/:id([0-9a-fA-F-]{32,36})`,component:placeholder})),
  {path:'/:pathMatch(.*)*',component:placeholder},
]});
