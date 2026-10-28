import os, subprocess
from pathlib import Path

# Start from a clean workspace: fresh out/ for the deliverable, and clear anything that is not one
# of the supplied April source files (leftover scratch/derived files from earlier attempts).
Path('out').mkdir(exist_ok=True)
sources = {
    'AR_Accrual-1.xlsx',
    'Accr2011-1.xlsx',
    'AccrBonus-1.xlsx',
    'AccrMisc-1.xlsx',
    'Aurisic_Corp_Payrolls_April_2025-1.xlsx',
    'Aurisic_Final_TB_4-25-1.txt',
    'Aurisic_Financials_3-25-1.xlsx',
    'Aurisic_Prepaid_Expenses_4-25-1.xlsx',
    'Good Insurance Co - Loan II.xlsx',
    'Good Insurance Co - Loan.xlsx',
    'Legal_Dump-1.xlsx',
    'Outstanding_CKs_4-30-25-1.xlsx',
    'PPD1250-1.xlsx',
    'PPD1251-1.xlsx',
    'Payroll-1.xlsx',
    'Prof_Fee_Dump-1.xlsx',
    'README.md',
    'Rebates-1.xlsx',
}
for p in sorted(Path('.').iterdir()):
    if p.name in sources or p.name == 'out':
        continue
    subprocess.run(['rm', '-rf', str(p)], check=True)
    print('removed', p.name)
print(sorted(os.listdir('.')))
