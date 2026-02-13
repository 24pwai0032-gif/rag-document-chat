"""
Advanced RAG System - Hugging Face Spaces Deployment
=====================================================

A production-ready RAG system with:
- Multi-format document support (PDF, DOCX, TXT)
- FAISS vector database
- HuggingFace LLM (no API key needed!)
- Interactive Gradio interface
"""

import os
import warnings
warnings.filterwarnings('ignore')

from typing import List, Dict, Any
import gradio as gr
from pathlib import Path
import time

# LangChain imports
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    Docx2txtLoader
)
from langchain.schema import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain_community.llms import HuggingFacePipeline
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline


class DocumentProcessor:
    """Handles document loading, chunking, and preprocessing."""
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def load_document(self, file_path: str) -> List[Document]:
        """Load a document based on its file extension."""
        file_extension = Path(file_path).suffix.lower()
        
        loaders = {
            '.pdf': PyPDFLoader,
            '.txt': TextLoader,
            '.docx': Docx2txtLoader
        }
        
        if file_extension not in loaders:
            raise ValueError(f"Unsupported file type: {file_extension}")
        
        loader_class = loaders[file_extension]
        loader = loader_class(file_path)
        documents = loader.load()
        
        for doc in documents:
            doc.metadata['source'] = Path(file_path).name
            doc.metadata['file_type'] = file_extension
        
        return documents
    
    def process_documents(self, file_paths: List[str]) -> List[Document]:
        """Process multiple documents: load and chunk."""
        all_documents = []
        
        for file_path in file_paths:
            try:
                docs = self.load_document(file_path)
                all_documents.extend(docs)
            except Exception as e:
                print(f"Error loading {file_path}: {str(e)}")
                continue
        
        chunked_docs = self.text_splitter.split_documents(all_documents)
        return chunked_docs


class VectorStoreManager:
    """Manages vector embeddings and FAISS index."""
    
    def __init__(self, embedding_model: str = "all-MiniLM-L6-v2"):
        self.embedding_model = embedding_model
        self.embeddings = HuggingFaceEmbeddings(
            model_name=embedding_model,
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
        self.vector_store = None
    
    def create_vector_store(self, documents: List[Document]) -> FAISS:
        """Create FAISS vector store from documents."""
        print("🔄 Creating embeddings...")
        self.vector_store = FAISS.from_documents(
            documents=documents,
            embedding=self.embeddings
        )
        print("✅ Vector store created!")
        return self.vector_store
    
    def save_vector_store(self, path: str = "faiss_index"):
        """Save FAISS index to disk."""
        if self.vector_store is None:
            raise ValueError("No vector store to save.")
        self.vector_store.save_local(path)
    
    def load_vector_store(self, path: str = "faiss_index") -> FAISS:
        """Load FAISS index from disk."""
        self.vector_store = FAISS.load_local(
            path,
            self.embeddings,
            allow_dangerous_deserialization=True
        )
        print("✅ Vector store loaded!")
        return self.vector_store


class RAGSystem:
    """Complete RAG system combining retrieval and generation."""
    
    def __init__(self, vector_store: FAISS, model_name: str = "google/flan-t5-base"):
        self.vector_store = vector_store
        
        print("📥 Loading language model (this may take a minute)...")
        
        # Load tokenizer and model locally
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        
        # Create pipeline
        pipe = pipeline(
            "text2text-generation",
            model=model,
            tokenizer=tokenizer,
            max_length=512,
            temperature=0.3
        )
        
        # Initialize LLM
        self.llm = HuggingFacePipeline(pipeline=pipe)
        
        print("✅ Language model loaded successfully!")
        
        self.prompt_template = PromptTemplate(
            template="""Answer the question based on the context below.

Context: {context}

Question: {question}

Answer:""",
            input_variables=["context", "question"]
        )
        
        self.qa_chain = RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=vector_store.as_retriever(search_kwargs={"k": 4}),
            return_source_documents=True,
            chain_type_kwargs={"prompt": self.prompt_template}
        )
    
    def query(self, question: str) -> Dict[str, Any]:
        """Query the RAG system."""
        try:
            result = self.qa_chain.invoke({"query": question})
            
            response = {
                "answer": result['result'],
                "sources": []
            }
            
            if 'source_documents' in result:
                response['sources'] = [
                    {
                        "content": doc.page_content[:200] + "...",
                        "source": doc.metadata.get('source', 'Unknown'),
                        "page": doc.metadata.get('page', 'N/A')
                    }
                    for doc in result['source_documents']
                ]
            
            return response
            
        except Exception as e:
            return {
                "answer": f"Error processing query: {str(e)}",
                "sources": []
            }


# Global variables
rag_system = None
doc_processor = DocumentProcessor()
vector_manager = VectorStoreManager()


def initialize_system():
    """Initialize the RAG system with pre-loaded documents."""
    global rag_system, vector_manager
    
    try:
        # Check if pre-loaded index exists
        if os.path.exists("faiss_index"):
            print("Loading pre-existing index...")
            vector_store = vector_manager.load_vector_store("faiss_index")
            rag_system = RAGSystem(vector_store)
            return "✅ System initialized with pre-loaded documents!"
        else:
            return "⚠️ No pre-loaded index found. Please upload documents."
    except Exception as e:
        return f"❌ Error initializing system: {str(e)}"


def process_uploaded_files(files, progress=gr.Progress()):
    """Process uploaded documents and create vector store."""
    global rag_system, doc_processor, vector_manager
    
    if not files:
        return "❌ Please upload at least one document."
    
    try:
        progress(0, desc="Starting document processing...")
        
        # Save uploaded files temporarily
        file_paths = []
        for file in files:
            file_paths.append(file.name)
        
        progress(0.3, desc="Loading and chunking documents...")
        documents = doc_processor.process_documents(file_paths)
        
        progress(0.6, desc="Creating embeddings (this may take a minute)...")
        vector_store = vector_manager.create_vector_store(documents)
        
        progress(0.9, desc="Initializing RAG system...")
        rag_system = RAGSystem(vector_store)
        
        progress(1.0, desc="Complete!")
        return f"✅ Successfully processed {len(files)} files and created {len(documents)} chunks!"
        
    except Exception as e:
        return f"❌ Error processing files: {str(e)}"


def chat_interface(message: str, history: List[List[str]]) -> str:
    """Process chat messages."""
    global rag_system
    
    if rag_system is None:
        return "⚠️ Please upload documents first and click 'Process Documents'."
    
    if not message.strip():
        return "Please enter a question."
    
    try:
        response = rag_system.query(message)
        answer = response['answer']
        
        if response['sources']:
            answer += "\n\n---\n**📚 Sources:**\n"
            for i, source in enumerate(response['sources'], 1):
                answer += f"\n{i}. **{source['source']}**\n"
                answer += f"   _{source['content']}_\n"
        
        return answer
        
    except Exception as e:
        return f"❌ Error: {str(e)}"


# Create Gradio interface
def create_interface():
    """Create the Gradio interface."""
    
    with gr.Blocks(theme=gr.themes.Soft(), title="Advanced RAG System") as demo:
        gr.Markdown("""
        # 🤖 Advanced RAG System
        
        Ask questions about your documents and get accurate, source-backed answers!
        
        ## How to use:
        1. Upload your documents (PDF, DOCX, TXT)
        2. Click "Process Documents" and wait for completion
        3. Ask questions in the chat interface below
        
        **No API keys needed - runs 100% free!** 🎉
        """)
        
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📤 Upload Documents")
                file_upload = gr.File(
                    label="Upload Documents",
                    file_count="multiple",
                    file_types=[".pdf", ".docx", ".txt"]
                )
                process_btn = gr.Button("📤 Process Documents", variant="primary", size="lg")
                upload_status = gr.Textbox(label="Processing Status", interactive=False, lines=2)
                
                gr.Markdown("""
                ### 📊 System Info
                - **Embedding**: all-MiniLM-L6-v2
                - **LLM**: FLAN-T5-Base
                - **Vector DB**: FAISS
                - **Chunk Size**: 1000/200
                """)
            
            with gr.Column(scale=2):
                chatbot = gr.ChatInterface(
                    fn=chat_interface,
                    title="💬 Chat with Your Documents",
                    examples=[
                        "What are the main topics covered?",
                        "Can you provide a summary?",
                        "What are the key findings?",
                        "What recommendations are mentioned?"
                    ],
                    theme=gr.themes.Soft()
                )
        
        # Event handlers
        process_btn.click(
            fn=process_uploaded_files,
            inputs=[file_upload],
            outputs=[upload_status]
        )
        
        gr.Markdown("""
        ---
        ### 💡 Tips for Best Results
        - Upload clear, well-formatted documents
        - Be specific in your questions
        - Ask follow-up questions to dive deeper
        - Check the sources provided with each answer
        
        ### ⚡ Features
        ✅ Multi-document support  
        ✅ Source citations  
        ✅ Fast semantic search  
        ✅ No API keys required  
        ✅ 100% free to use  
        """)
    
    return demo


if __name__ == "__main__":
    demo = create_interface()
    demo.launch()