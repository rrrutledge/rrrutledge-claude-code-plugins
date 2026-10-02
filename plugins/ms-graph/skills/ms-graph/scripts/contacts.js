// Read / create / update personal Outlook contacts (People) via Microsoft Graph.
//
// List:    node contacts.js --list [--top=50]
//          (all contacts, alphabetical by display name)
// Search:  node contacts.js --search="Jamie"
// Show:    node contacts.js --show=<id>
//          (full contact, including addresses)
// Create:  node contacts.js --create --name="Jamie Lin" [--email=a@x] [--phone=555-1234] \
//            [--company=Acme] [--job-title=Director]
//          (creates a new personal contact; requires the Contacts.ReadWrite scope)
// Update:  node contacts.js --update=<id> [--street="123 Main St"] [--city=...] [--state=...] \
//            [--zip=...] [--country=...] [--address-type=home|business|other]
//          (patches one address type on an existing contact, preserving any fields not passed;
//           --address-type defaults to home)

const { getGraphClient } = require('./graph-client');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);

const CONTACT_FIELDS = 'id,displayName,emailAddresses,mobilePhone,businessPhones,companyName,jobTitle,homeAddress,businessAddress,otherAddress';

function printAddress(label, addr) {
  if (!addr || !Object.keys(addr).length) return;
  const parts = [addr.street, addr.city, addr.state, addr.postalCode, addr.countryOrRegion].filter(Boolean);
  if (parts.length) console.log(`    ${label}: ${parts.join(', ')}`);
}

function printContact(c) {
  console.log(`\n--- ${c.displayName}`);
  if ((c.emailAddresses || []).length) console.log(`    email: ${c.emailAddresses.map(e => e.address).join(', ')}`);
  if ((c.mobilePhone || (c.businessPhones || []).length)) {
    const phones = [c.mobilePhone, ...(c.businessPhones || [])].filter(Boolean);
    console.log(`    phone: ${phones.join(', ')}`);
  }
  if (c.companyName) console.log(`    company: ${c.companyName}${c.jobTitle ? ` (${c.jobTitle})` : ''}`);
  printAddress('home', c.homeAddress);
  printAddress('business', c.businessAddress);
  printAddress('other', c.otherAddress);
  console.log(`    id: ${c.id}`);
}

async function list(client) {
  const data = await client.api('/me/contacts')
    .orderby('displayName')
    .top(parseInt(args.top || '50', 10))
    .select('id,displayName,emailAddresses,mobilePhone,businessPhones,companyName,jobTitle')
    .get();
  const contacts = data.value || [];
  if (!contacts.length) { console.log('No contacts.'); return; }
  console.log(`${contacts.length} contact(s):`);
  contacts.forEach(printContact);
}

async function search(client) {
  const data = await client.api('/me/contacts')
    .filter(`startswith(displayName,'${String(args.search).replace(/'/g, "''")}')`)
    .select('id,displayName,emailAddresses,mobilePhone,businessPhones,companyName,jobTitle')
    .get();
  const contacts = data.value || [];
  if (!contacts.length) { console.log('No matching contacts.'); return; }
  contacts.forEach(printContact);
}

async function show(client) {
  const contact = await client.api(`/me/contacts/${args.show}`).select(CONTACT_FIELDS).get();
  printContact(contact);
}

async function create(client) {
  if (!args.name) throw new Error('--create requires --name');
  const contact = { displayName: args.name };
  if (args.email) contact.emailAddresses = [{ address: args.email, name: args.name }];
  if (args.phone) contact.mobilePhone = args.phone;
  if (args.company) contact.companyName = args.company;
  if (args['job-title']) contact.jobTitle = args['job-title'];
  const created = await client.api('/me/contacts').post(contact);
  console.log(`Created contact "${created.displayName}" (id ${created.id}).`);
}

async function update(client) {
  const addressType = String(args['address-type'] || 'home').toLowerCase();
  const field = { home: 'homeAddress', business: 'businessAddress', other: 'otherAddress' }[addressType];
  if (!field) throw new Error('--address-type must be home, business, or other');

  const existing = await client.api(`/me/contacts/${args.update}`).select(field).get();
  const addr = Object.assign({}, existing[field] || {});
  if (args.street) addr.street = args.street;
  if (args.city) addr.city = args.city;
  if (args.state) addr.state = args.state;
  if (args.zip) addr.postalCode = args.zip;
  if (args.country) addr.countryOrRegion = args.country;

  const updated = await client.api(`/me/contacts/${args.update}`).patch({ [field]: addr });
  console.log(`Updated "${updated.displayName}" ${addressType} address.`);
  printAddress(addressType, updated[field]);
}

if (require.main === module) {
  (async () => {
    const client = await getGraphClient();
    if (args.list) return list(client);
    if (args.search) return search(client);
    if (args.show) return show(client);
    if (args.create) return create(client);
    if (args.update) return update(client);
    throw new Error('Specify --list, --search, --show, --create, or --update');
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
