"""PDF findings export from accepted, escaped report content."""
import io
from html import escape
from pathlib import Path

def make_pdf(item):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if 'FlowPilotVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('FlowPilotVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    styles=getSampleStyleSheet()
    for name in ('Normal','Title','Heading1','Heading2'):
        styles[name].fontName='FlowPilotVera'
    styles['Normal'].fontSize=10;styles['Normal'].leading=15
    styles['Title'].textColor=colors.HexColor('#285a43')
    buffer=io.BytesIO();document=SimpleDocTemplate(buffer,title='FlowPilot analysis',author='FlowPilot',leftMargin=42,rightMargin=42,topMargin=42,bottomMargin=42)
    story=[Paragraph('FlowPilot · Analysis findings',styles['Title']),Spacer(1,16),Paragraph(escape(item.get('request') or item.get('title') or 'Dataset analysis'),styles['Normal']),Spacer(1,12),Paragraph('Workflow version '+str(item.get('revision',1))+' · '+escape(item['status']),styles['Normal'])]
    insights=item['insights']
    sections=insights.get('sections') or [{'title':'Summary','findings':[insights.get('summary','')]+[f.get('text','') for f in insights.get('findings',[])]}]
    for section in sections:
        story.extend([Spacer(1,16),Paragraph(escape(section['title']),styles['Heading2'])])
        for text in section['findings']:story.extend([Paragraph(escape(str(text)).replace('\n','<br/>'),styles['Normal']),Spacer(1,7)])
        for cite in section.get('citations',[]):story.append(Paragraph(escape(f"Source: {cite['filename']}, page/section {cite['page']}, passage {cite['chunk_id']}"),styles['Normal']))
    story.extend([Spacer(1,18),Paragraph('Findings are derived from accepted workflow outputs. Export the calculated results as CSV or Excel for the full underlying records.',styles['Normal'])])
    document.build(story)
    return buffer.getvalue()
