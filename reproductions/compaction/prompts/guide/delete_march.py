import os, subprocess
from pathlib import Path

# Start from a clean workspace: fresh out/ for deliverables, and clear anything that is not one
# of the supplied source attachments (leftover scratch/derived files from earlier attempts).
Path('out').mkdir(exist_ok=True)
sources = {
    'README.md',
    'COA.xlsx',
    'Aurisic_Prepaid_Insurance.pdf',
    'Aurisic_Prepaid_Expenses_Jan25.pdf',
    'Aurisic_Prepaid_Expenses_Feb25.pdf',
    'Aurisic_Prepaid_Expenses_Apr25.pdf',
}
for p in sorted(Path('.').iterdir()):
    if p.name in sources or p.name == 'out':
        continue
    subprocess.run(['rm', '-rf', str(p)], check=True)
    print('removed', p.name)
print(sorted(os.listdir('.')))
