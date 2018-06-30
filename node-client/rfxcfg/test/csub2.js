
const getcfg = require('../index.js')

exports.moar = async function () {
  const config = await getcfg.load()
  console.log('second')
  console.log(config)
}
