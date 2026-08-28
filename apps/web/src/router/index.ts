import { createRouter, createWebHistory } from 'vue-router';
import Login from '../views/Login.vue'; import Dashboard from '../views/Dashboard.vue'; import Resource from '../views/Resource.vue';
const routes=[{path:'/login',component:Login},{path:'/',redirect:'/recruitment/overview'},
{path:'/recruitment/overview',component:Dashboard,meta:{title:'招聘概览'}},
...['conflicts','candidates','interviews','jobs','accounts','recruiters','notifications','plugin-diagnostics','audit-logs','settings','mock-feishu'].map(name=>({path:`/recruitment/${name}`,component:Resource,props:{resource:name},meta:{title:name}}))];
const router=createRouter({history:createWebHistory(),routes});
router.beforeEach(to=>{if(to.path!='/login'&&!localStorage.getItem('access_token'))return '/login';});
export default router;

