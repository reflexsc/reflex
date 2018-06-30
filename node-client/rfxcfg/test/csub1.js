
const getcfg = require('../index.js')

exports.moar = async function () {
  const config = await getcfg.load()
  console.log('first')
  console.log(config)
}
