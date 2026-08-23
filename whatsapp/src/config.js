const dotenv = require('dotenv');
dotenv.config({ path: require('path').resolve(__dirname, '..', '..', '.env') });

function getEnv(name, fallback = '') {
  const value = process.env[name];
  return value === undefined ? fallback : String(value).trim();
}

function getIntEnv(name, fallback) {
  const value = Number(getEnv(name, fallback));
  return Number.isFinite(value) ? value : fallback;
}

function getBoolEnv(name, fallback = false) {
  const value = getEnv(name, fallback ? 'true' : 'false').toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(value);
}

module.exports = {
  getEnv,
  getIntEnv,
  getBoolEnv,
};
