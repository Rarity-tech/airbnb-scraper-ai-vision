// screenshot_and_send.js - Phase 2: Screenshot + Envoi n8n
import puppeteer from 'puppeteer';
import fetch from 'node-fetch';
import fs from 'fs';
import Papa from 'papaparse';

const N8N_WEBHOOK = process.env.N8N_WEBHOOK_URL;
const SOURCE_WORKFLOW = process.env.SOURCE_WORKFLOW || 'unknown_run';

if (!N8N_WEBHOOK) {
  console.error('❌ N8N_WEBHOOK_URL non défini dans les secrets GitHub!');
  process.exit(1);
}

// Lire le CSV de Phase 1
const csvContent = fs.readFileSync('airbnb_results.csv', 'utf8');
const parsed = Papa.parse(csvContent, { header: true, encoding: 'utf-8-sig' });
const listings = parsed.data.filter(row => 
  row.host_profile_url && 
  row.host_profile_url.trim() &&
  row.host_profile_url.startsWith('http')
);

console.log(`\n📊 ${listings.length} hôtes à traiter\n`);

async function screenshotAndSendHost(listing, index, total) {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });
  
  const page = await browser.newPage();
  
  try {
    console.log(`\n${'='.repeat(60)}`);
    console.log(`📸 [${index + 1}/${total}] Processing host...`);
    console.log(`   Listing: ${listing.url}`);
    console.log(`   Host: ${listing.host_profile_url}`);
    console.log(`${'='.repeat(60)}`);
    
    // Configuration
    await page.setViewport({ width: 1920, height: 3000 });
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36');
    
    // Bloquer ressources lourdes
    await page.setRequestInterception(true);
    page.on('request', req => {
      const type = req.resourceType();
      if (['image', 'font', 'media'].includes(type)) {
        req.abort();
      } else {
        req.continue();
      }
    });
    
    // Navigation
    console.log('⏳ Navigation vers le profil...');
    await page.goto(listing.host_profile_url, { 
      waitUntil: 'networkidle0', 
      timeout: 60000 
    });
    
    // ⏰ ATTENTE CRITIQUE : 15 secondes
    console.log('⏳ Attente 15 secondes pour chargement complet...');
    await new Promise(r => setTimeout(r, 15000));
    
    // Scroll
    console.log('📜 Scroll pour charger contenu...');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await new Promise(r => setTimeout(r, 2000));
    await page.evaluate(() => window.scrollTo(0, 0));
    await new Promise(r => setTimeout(r, 1000));
    
    // Screenshot
    console.log('📸 Capture screenshot...');
    const screenshot = await page.screenshot({
      fullPage: true,
      type: 'jpeg',
      quality: 80
    });
    
    const base64 = screenshot.toString('base64');
    console.log(`✓ Screenshot capturé (${(base64.length / 1024).toFixed(0)} KB)`);
    
    // Payload complet
    const payload = {
      listing_url: listing.url || '',
      listing_title: listing.title || '',
      license_code: listing.license_code || '',
      host_url: listing.host_profile_url || '',
      scraped_at: listing.scraped_at || new Date().toISOString(),
      source_workflow: SOURCE_WORKFLOW,
      screenshot_base64: base64,
      processing_index: index + 1,
      total_to_process: total
    };
    
    // Envoi vers n8n
    console.log('🌐 Envoi vers n8n...');
    const response = await fetch(N8N_WEBHOOK, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'User-Agent': 'GitHub-Actions-Airbnb-Scraper'
      },
      body: JSON.stringify(payload),
      timeout: 120000
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const result = await response.json();
    console.log(`✅ Réponse n8n:`, result);
    console.log(`   Status: ${result.status || 'unknown'}`);
    console.log(`   Host: ${result.host_name || 'N/A'}`);
    
    await browser.close();
    
    return {
      success: true,
      listing_url: listing.url,
      host_url: listing.host_profile_url,
      result: result
    };
    
  } catch (error) {
    console.error(`❌ Erreur:`, error.message);
    await browser.close();
    
    return {
      success: false,
      listing_url: listing.url,
      host_url: listing.host_profile_url,
      error: error.message
    };
  }
}

// Traitement séquentiel
async function main() {
  console.log('\n🚀 DÉBUT DU TRAITEMENT SÉQUENTIEL');
  console.log('='.repeat(60) + '\n');
  
  const results = [];
  
  for (let i = 0; i < listings.length; i++) {
    const result = await screenshotAndSendHost(listings[i], i, listings.length);
    results.push(result);
    
    if (i < listings.length - 1) {
      console.log('\n⏳ Pause 3 secondes avant le suivant...\n');
      await new Promise(r => setTimeout(r, 3000));
    }
  }
  
  // Résumé
  console.log('\n' + '='.repeat(60));
  console.log('🎉 TRAITEMENT TERMINÉ');
  console.log('='.repeat(60));
  
  const successful = results.filter(r => r.success).length;
  const failed = results.filter(r => !r.success).length;
  
  console.log(`\n📊 RÉSUMÉ:`);
  console.log(`   Total: ${results.length}`);
  console.log(`   ✅ Succès: ${successful}`);
  console.log(`   ❌ Échecs: ${failed}`);
  
  if (failed > 0) {
    console.log(`\n⚠️ Hosts en échec:`);
    results.filter(r => !r.success).forEach(r => {
      console.log(`   - ${r.host_url}: ${r.error}`);
    });
  }
  
  fs.writeFileSync('phase2_report.json', JSON.stringify(results, null, 2));
  console.log(`\n💾 Rapport: phase2_report.json\n`);
}

main().catch(err => {
  console.error('💥 ERREUR FATALE:', err);
  process.exit(1);
});
