import test from 'node:test';
import assert from 'node:assert/strict';
import { apiBase } from './apiBase.js';

test('sin VITE_API_URL se usa /api, como en Render', () => {
  assert.equal(apiBase(undefined), '/api');
  assert.equal(apiBase(''), '/api');
});

test('con VITE_API_URL se llama directo al backend, sin la barra final', () => {
  assert.equal(apiBase('https://sigpa-34sy.onrender.com'), 'https://sigpa-34sy.onrender.com');
  assert.equal(apiBase(' https://sigpa-34sy.onrender.com/ '), 'https://sigpa-34sy.onrender.com');
});
