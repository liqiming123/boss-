import {mount} from '@vue/test-utils';
import {createPinia,setActivePinia} from 'pinia';
import {beforeEach,describe,expect,it} from 'vitest';
import Login from '../src/views/Login.vue';

beforeEach(()=>setActivePinia(createPinia()));

describe('Login',()=>{
  it('offers only Feishu identity verification',()=>{
    const wrapper=mount(Login,{global:{stubs:{'el-button':{template:'<button><slot/></button>'}}}});
    expect(wrapper.text()).toContain('招聘协同运维台');
    expect(wrapper.text()).toContain('使用飞书登录');
    expect(wrapper.text()).not.toContain('管理员邮箱');
    expect(wrapper.find('input').exists()).toBe(false);
  });
});
