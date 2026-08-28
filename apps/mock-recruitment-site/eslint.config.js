import js from '@eslint/js'; import tseslint from 'typescript-eslint'; import pluginVue from 'eslint-plugin-vue';
export default [...pluginVue.configs['flat/recommended'], js.configs.recommended, ...tseslint.configs.recommended, {languageOptions:{globals:{history:'readonly',setTimeout:'readonly'}}}];
