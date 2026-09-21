I need help with a arxiv litreview analysis. There is external benchmark data available that I need you to fetch the data from http://localhost:30323/api/data.json and extract the relevant metrics.

Then search for relevant academic papers on the topic.

Use the terminal to create and run a Python script called arxiv_litreview_processor.py in the workspace that reads the collected data from JSON files you create, performs the analysis, and outputs arxiv_litreview_results.json.

Create an Excel file called Lit_Review_Report.xlsx with three sheets. The first sheet Data_Analysis should contain the main comparison data with relevant columns. The second sheet Metrics should summarize key metrics. The third sheet Recommendations should list actionable items.

The Data_Analysis sheet should include columns for the primary dimension (such as department, product, region, or topic), our internal metric values, the external benchmark values, and the gap or difference between them. Sort the data alphabetically by the primary dimension. The Metrics sheet should have two columns Metric and Value summarizing total counts, averages, and key statistics. The Recommendations sheet should list priority actions based on the gap analysis. Also create a Word document called Lit_Review_Analysis.docx with an executive summary, key findings, and recommendations sections. Create a Google Sheet called "Arxiv Litreview Tracker" with the key data points.

Required workbook layout: Data_Analysis must have at least 4 data rows and columns Paper_ID, Title, Area, Citations, Relevance_Score; Metrics must have at least 4 data rows and columns Metric, Value; Recommendations must have at least 2 data rows and columns Priority, Action, Area. Additional columns are welcome for benchmark values and comparisons.

Use one row per relevant retrieved paper; Area should link it to a trending topic in the benchmark. Include the external topic growth rate and your relevance rationale in the report. Relevance_Score is your assessment on a 1–5 scale. Use 0 when the archive does not provide citation counts.
