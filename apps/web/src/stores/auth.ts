import { defineStore } from 'pinia';
import { ref } from 'vue';
import { ApiClient } from '@recruitment/api-client';
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('access_token'));
  const user = ref<{display_name:string;role:string}|null>(JSON.parse(localStorage.getItem('user') ?? 'null'));
  const client = new ApiClient(import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1', () => token.value);
  async function login(email:string,password:string){const data=await client.login(email,password); token.value=data.access_token;user.value=data.user;localStorage.setItem('access_token',data.access_token);localStorage.setItem('refresh_token',data.refresh_token);localStorage.setItem('user',JSON.stringify(data.user));}
  function logout(){token.value=null;user.value=null;localStorage.clear();}
  return { token,user,client,login,logout };
});

