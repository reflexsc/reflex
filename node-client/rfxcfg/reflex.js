'use strict'

const uuid4 = require('uuid/v4')
// const base64 = require('base-64')
const jwt = require('jsonwebtoken')

// const rax = require('retry-axios')
const axios = require('axios')

// Notes: Axios should be able to do this simply with an intercept, but it is
// not catching all errors (notably ECONNREFUSED, which is the most important).
// As such I had to step us back in time a little and force some synchronous
// calls.  Getting configuration is mandatory before we can move on with the
// rest of code - BJG

// Leaving this here incase some day things improve:
/*
// setup retries with axios
const axios = Axios.create()
axios.defaults = {
  raxConfig: {
    retry: 5,
    retryDelay: 10,
    httpStatusCodesToRetry: [[100, 199], [429, 429], [500, 599]],
    onRetryAttempt: (err) => {
      const rcfg = rax.getConfig(err)
      console.log(`Retry attempt #${cfg.currentRetryAttempt}`)
    }
  }
}
const interceptorId = rax.attach(axios)
*/

// ****************************************************************************
// Outer session object

function Session (cfg) {
  const apikey = process.env.REFLEX_APIKEY || cfg.REFLEX_APIKEY
  this.url = process.env.REFLEX_URL || cfg.REFLEX_URL
  if (!apikey) {
    throw new Error('Missing REFLEX_APIKEY')
  }
  if (!this.url) {
    throw new Error('Missing REFLEX_URL')
  }
  [this.apikeyId, this.apikeySecret] = apikey.split('.')
  // something is broken, don't use the binary secrets, stick w/base64
  //  this.apikeySecret = base64.decode(this.apikeySecret)
  this.cache = {}
  this.headers = {}
  this.expires = 0
  this.token = ''
  this.payload = {}
  this.secret = ''
}

// ****************************************************************************
// uglyness for lack of proper error handling w/axios
function sleep (ms) {
  return new Promise(resolve => {
    setTimeout(resolve, ms)
  })
}

// more uglyness to bodger this synchronous, as axios's intercepts aren't catching all errors
async function retryAxios (url, opts, response) {
  // wait a minute
  let retries = 60
  let wait = 1000
  while (true) {
    try {
      return await axios.get(url, opts)
        .then(response)
    } catch (err) {
      retries--
      if (retries <= 0) {
        // future: I can probably handle this with an axios.create // axios.error
        // return undefined
        throw (err)
      }
      await sleep(wait)
    }
  }
}

// ****************************************************************************
// login to reflex using the API key, get a session
//
Session.prototype.login = async function (force) {
  /* note: test & evaluate w/millisecs and posix time on expires */
  if (!force && this.expires > Date.now()) {
    return
  }

  // generate a new refreshToken (for signing in)
  const refreshToken = jwt.sign({
    seed: uuid4(),
    jti: this.apikeyId,
    exp: (Date.now() / 1000) + 300
  }, this.apikeySecret)

  let data = await retryAxios(this.url + '/token', {
    headers: {
      'X-Apikey': refreshToken
    }}, response => {
    if (response.status !== 200) {
      throw new Error('Unable to login to reflex at ' + this.url)
    }
    return response.data
  })

  if (data === undefined) {
    throw new Error('Unable to login to reflex at ' + this.url)
  }
  this.payload = {
    sid: data.session,
    jti: data.jti,
    exp: data.expires_at
  }
  this.secret = data.secret // base64.decode(data.secret)
  this.expires = data.expires_at * 1000
  this.getAccessToken()
}

// ****************************************************************************
// generate an accessToken (only need to do this occasionally)
Session.prototype.getAccessToken = async function (name) {
  if (this.token && this.expires > Date.now()) {
    return
  }
  this.token = await jwt.sign(this.payload, this.secret)
}

// ****************************************************************************
// get a config object from Reflex
Session.prototype.getConfig = async function (name) {
  // login to reflex, if needed
  await this.login()

  const target = this.url + '/config/' + name

  // get it<removed await>
  return retryAxios(target, {
    headers: {
      'X-ApiToken': this.token,
      'Cookie': 'sid=' + this.payload.sid
    }
  }, response => {
    if (response.status !== 200) {
      throw new Error('Unable to login to reflex at ' + this.url)
    }
    return response.data
  })
}

module.exports.Session = Session
