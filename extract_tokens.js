// Run this in Chrome DevTools Console on https://internal-transfer.talent.amazon.dev
// Copy the output and paste into your .env file

const clientId = '6hr71icfdda6n67uvvm3nvlu4d';
const username = 'AmazonFederateOIDC_<amazon_user_alias>';  // Replace <amazon_user_alias> with your Amazon user alias (e.g. ajassy)

const idToken = localStorage.getItem(`CognitoIdentityServiceProvider.${clientId}.${username}.idToken`);
const refreshToken = localStorage.getItem(`CognitoIdentityServiceProvider.${clientId}.${username}.refreshToken`);

console.log('Copy these to your .env file:\n');
console.log(`COGNITO_ID_TOKEN='${idToken}'`);
console.log(`\nCOGNITO_REFRESH_TOKEN='${refreshToken}'`);
