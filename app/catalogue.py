CATALOGUE={
'validate_dataset':{'inputs':{'dataset':'dataset'},'output':'validated','memory_mb':128},
'clean_transactions':{'inputs':{'validated':'validated'},'output':'cleaned','memory_mb':128},
'monthly_sales':{'inputs':{'cleaned':'cleaned'},'output':'monthly','memory_mb':128},
'product_summary':{'inputs':{'cleaned':'cleaned'},'output':'products','memory_mb':128},
'flag_unusual_amounts':{'inputs':{'cleaned':'cleaned'},'output':'flags','memory_mb':128},
'filter_transactions':{'inputs':{'cleaned':'cleaned'},'output':'cleaned','memory_mb':128},
'build_report':{'inputs':{'monthly':'monthly','products':'products','flags':'flags','cleaned':'cleaned'},'optional_inputs':['monthly','products','flags','cleaned'],'output':'report','memory_mb':128}}
# Human operation labels accepted only by the intent compiler; executable handlers stay canonical.
INTENT_ALIASES={'calculate_monthly_sales':'monthly_sales','calculate_product_performance':'product_summary','product_performance':'product_summary','generate_report':'build_report','validate_transactions':'validate_dataset','clean_data':'clean_transactions','flag_unusual_transactions':'flag_unusual_amounts'}

# Generic operations keep legacy handlers and their artifact contracts intact.
for _op in ('load','profile','convert','filter','join','derive','aggregate','time','sort','outliers','deduplicate','fill_missing','report'):
    CATALOGUE['table_'+_op]={'inputs':{'dataset':'dataset'} if _op=='load' else {'left':'table','right':'table'} if _op=='join' else {} if _op=='report' else {'table':'table'},'output':'report' if _op=='report' else 'table','memory_mb':128}
CATALOGUE.update({
    'document_load':{'inputs':{'dataset':'dataset'},'output':'document','memory_mb':128},
    'document_search':{'inputs':{},'output':'evidence','memory_mb':128},
    'document_report':{'inputs':{'evidence':'evidence'},'output':'report','memory_mb':128},
})
