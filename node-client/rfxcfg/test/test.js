
const getcfg = require('../index.js')
const csub1 = require('./csub1')
const csub2 = require('./csub2')

async function main () {
  const config = await getcfg.load()
  console.log('outer')
  console.log(config)
  csub1.moar()
  csub2.moar()
}

main()
