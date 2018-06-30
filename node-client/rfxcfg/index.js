'use strict'
const reflex = require('./reflex')
const fs = require('fs')

const globalConfig = {
  data: undefined,
  REFLEX_URL: '',
  REFLEX_APIKEY: '',
  REFLEX_SERVICE: '',
  REFLEX_CONFIG: ''
}

exports.load = async function () {
  console.log(globalConfig)
  if (globalConfig.data !== undefined) {
    return globalConfig.data
  }

  // bring in the reflex meta-config first, from secrets, or procs
  for (let name in globalConfig) {
    if (name === undefined) {
      break
    }
    const fname = '/run/secrets/' + name
    if (fs.existsSync(fname)) {
      globalConfig[name] = fs.readFileSync(fname)
    } else {
      globalConfig[name] = process.env[name]
    }
  }
  const rfx = new reflex.Session(globalConfig)
  // check service and pivot off that?
  globalConfig.data = await rfx.getConfig(globalConfig.REFLEX_CONFIG)
  return globalConfig.data
}
