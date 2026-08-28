export type AuthState={accessToken?:string;refreshToken?:string;apiBaseUrl:string};const defaults:AuthState={apiBaseUrl:'http://localhost:8000/api/v1'};
export async function getAuth():Promise<AuthState>{return{...defaults,...await chrome.storage.local.get(['accessToken','refreshToken','apiBaseUrl'])}as AuthState}export async function setAuth(value:Partial<AuthState>){await chrome.storage.local.set(value)}export async function clearAuth(){await chrome.storage.local.remove(['accessToken','refreshToken'])}

