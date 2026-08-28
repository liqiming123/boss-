import js from'@eslint/js';import tseslint from'typescript-eslint';export default tseslint.config(js.configs.recommended,...tseslint.configs.recommended,{languageOptions:{globals:{chrome:'readonly',MutationObserver:'readonly',document:'readonly',window:'readonly',location:'readonly',HTMLElement:'readonly',customElements:'readonly'}}});

